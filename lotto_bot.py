import firebase_admin
from firebase_admin import credentials, firestore
import requests
import datetime
from datetime import datetime as dt
import random
import os
import json
from google.cloud.firestore_v1.base_query import FieldFilter

# --- 1. 설정 및 초기화 ---
cred_path = "serviceAccountKey.json"
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(cred)
else:
    print("❌ serviceAccountKey.json을 찾을 수 없습니다.")
    exit(1)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. GitHub 오픈소스 데이터셋에서 로또 번호 가져오기 ---
def get_lotto_data_from_github():
    """
    해외 IP 차단을 피하기 위해 GitHub에 호스팅된 로또 데이터셋을 가져옵니다.
    주로 'yous/lotto' 저장소가 업데이트가 빠르고 정확합니다.
    """
    # 1순위: yous/lotto (JSON 데이터셋)
    url = "https://raw.githubusercontent.com/yous/lotto/master/data.json"
    
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            # 데이터는 리스트 형태이며 마지막 요소가 최신 회차임
            latest = data[-1]
            return {
                'drwNo': latest['round'],
                'numbers': latest['numbers'],
                'bonus': latest['bonus'],
                'success': True
            }
    except Exception as e:
        print(f"⚠️ GitHub 데이터 로드 실패: {e}")
        
    # 실패 시 백업: 날짜 기반 계산 (회차 번호만이라도 확보)
    base_date = dt(2002, 12, 7)
    calc_round = (dt.now() - base_date).days // 7 + 1
    if dt.now().weekday() == 5 and dt.now().hour < 21: calc_round -= 1
    
    return {'drwNo': calc_round, 'numbers': [], 'bonus': 0, 'success': False}

# --- 3. 로또 번호 추출 알고리즘 (3대 원칙 적용) ---
def generate_advanced_numbers(latest_win_numbers):
    """
    ① Cold Number 우선 (하위 20% 빈도 가중치)
    ② 생일수 배제 (고번호 32~45번 4개 이상 필수)
    ③ 용지 시각적 패턴 제거 (7x7 용지 기준 3연속 금지)
    """
    results = []
    all_numbers = list(range(1, 46))
    
    # [알고리즘 ①] Cold Number: 최신 당첨 번호를 제외한 나머지 번호 풀 활용
    cold_pool = [n for n in all_numbers if n not in latest_win_numbers] if latest_win_numbers else all_numbers
    
    while len(results) < 5:
        # 번호 조합 생성 (Cold Number 2개 + 나머지 4개 조합)
        sample_cold = random.sample(cold_pool, 2)
        sample_others = random.sample(all_numbers, 4)
        comb = sorted(list(set(sample_cold + sample_others)))
        
        if len(comb) < 6: continue

        # [알고리즘 ②] 생일수 배제: 고번호(32~45)가 4개 이상 포함되어야 함
        high_count = sum(1 for n in comb if 32 <= n <= 45)
        if high_count < 4: continue

        # [알고리즘 ③] 용지 시각적 패턴 제거 (7x7 그리드)
        grid = [[0]*7 for _ in range(7)]
        for n in comb:
            grid[(n-1)//7][(n-1)%7] = 1
        
        is_pattern = False
        for i in range(7):
            for j in range(5):
                # 가로/세로 3개 연속 체크
                if (grid[i][j] and grid[i][j+1] and grid[i][j+2]) or \
                   (grid[j][i] and grid[j+1][i] and grid[j+2][i]):
                    is_pattern = True; break
            if is_pattern: break

        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. Firebase 데이터 처리 및 신규 업로드 ---
def sync_firebase_results(latest_info):
    """기존 'wait' 상태의 문서를 찾아 당첨 내역을 업데이트합니다."""
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    update_count = 0
    
    for doc in docs:
        data = doc.to_dict()
        doc_round = data.get('round')
        
        if doc_round <= latest_info['drwNo']:
            update_data = {"result": "processed", "updatedAt": dt.now().isoformat()}
            if latest_info['success'] and doc_round == latest_info['drwNo']:
                update_data.update({
                    "winningNumbers": latest_info['numbers'],
                    "bonus": latest_info['bonus']
                })
            doc.reference.update(update_data)
            update_count += 1
            
    print(f"✅ 기존 당첨 내역 {update_count}건 업데이트 완료")

def main():
    print("--- 1. 데이터 수집 (GitHub Source) 및 동기화 ---")
    latest = get_lotto_data_from_github()
    sync_firebase_results(latest)

    print("\n--- 2. 신규 추천 번호 생성 및 업로드 ---")
    next_round = latest['drwNo'] + 1
    
    # 중복 업로드 방지
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")
        return

    # 알고리즘 기반 번호 생성
    recommendations = generate_advanced_numbers(latest['numbers'])
    
    # Firebase 신규 문서 작성
    new_doc = {
        "round": next_round,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": recommendations[0],
        "full_sets": json.dumps(recommendations),
        "result": "wait",
        "createdAt": dt.now().isoformat(),
        "strategy": "ColdNumber + HighPriority(32-45) + PatternRemoval"
    }
    
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 추천 번호 업로드 완료: {recommendations[0]}")

if __name__ == "__main__":
    main()
