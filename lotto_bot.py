import firebase_admin
from firebase_admin import credentials, firestore
import requests
import datetime
from datetime import datetime as dt
import random
from collections import Counter
import os
import json
from bs4 import BeautifulSoup
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
    print("❌ 에러: serviceAccountKey.json 파일을 찾을 수 없습니다.")
    exit(1)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. 데이터 수집 (스크래핑 + 백업 계산 로직) ---
def get_lotto_data_safe():
    """웹 스크래핑으로 최신 당첨 정보를 가져오며, 실패 시 날짜 기반으로 회차를 계산합니다."""
    url = "https://dhlottery.co.kr/gameResult.do?method=byWin"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 회차 번호 추출
        round_tag = soup.find('strong', id='lottoDrwNo') or soup.select_one('.win_result strong')
        cur_round = int(round_tag.text.replace('회', '').strip())
        
        # 당첨 번호 6개 추출
        win_div = soup.find('div', class_='num win')
        win_list = win_div.find_all('span', class_='ball_645')
        numbers = [int(n.text) for n in win_list]
        
        # 보너스 번호 추출
        bonus_div = soup.find('div', class_='num bonus')
        bonus = int(bonus_div.find('span', class_='ball_645').text)
        
        return {'drwNo': cur_round, 'numbers': numbers, 'bonus': bonus, 'success': True}
    except Exception as e:
        print(f"⚠️ 스크래핑 실패 ({e}): 날짜 기반 계산으로 전환합니다.")
        base_date = dt(2002, 12, 7) # 1회차 기준일
        calc_round = (dt.now() - base_date).days // 7 + 1
        return {'drwNo': calc_round, 'numbers': [], 'bonus': 0, 'success': False}

# --- 3. 로또 번호 추출 알고리즘 (3대 원칙 적용) ---
def generate_advanced_numbers(latest_win_numbers):
    """
    ① Cold Number 우선 (빈도 하위 20% 가중치)
    ② 생일수 배제 (고번호 32~45번 4개 이상 필수)
    ③ 시각적 패턴 제거 (7x7 용지 기준 직선/대각선 3연속 금지)
    """
    results = []
    all_numbers = list(range(1, 46))
    
    # [알고리즘 ①] Cold Number 후보군 설정 (최근 당첨번호 제외군)
    cold_pool = [n for n in all_numbers if n not in latest_win_numbers] if latest_win_numbers else all_numbers
    
    while len(results) < 5:
        # 번호 조합 생성 (Cold Number 2개 + 나머지 4개 조합)
        sample_cold = random.sample(cold_pool, 2)
        sample_others = random.sample(all_numbers, 4)
        comb = sorted(list(set(sample_cold + sample_others)))
        
        if len(comb) < 6: continue

        # [알고리즘 ②] 생일수 배제: 고번호(32~45)가 4개 이상 포함되어야 함
        high_count = sum(1 for n in comb if 32 <= n <= 45)
        if high_count < 4:
            continue

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
            
        # 대각선 3개 연속 체크
        for r in range(5):
            for c in range(5):
                if grid[r][c] and grid[r+1][c+1] and grid[r+2][c+2]: is_pattern = True

        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. Firebase 데이터 처리 (복원된 기능) ---
def sync_firebase_results(latest_info):
    """기존 'wait' 상태의 문서를 찾아 당첨 내역을 업데이트합니다."""
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    update_count = 0
    
    for doc in docs:
        data = doc.to_dict()
        doc_round = data.get('round')
        
        # 현재 회차보다 이전이거나 같은 회차라면 결과 업데이트
        if doc_round <= latest_info['drwNo']:
            update_data = {
                "result": "processed",
                "updatedAt": dt.now().isoformat()
            }
            # 스크래핑 성공 시 실제 당첨번호 기록
            if latest_info['success'] and doc_round == latest_info['drwNo']:
                update_data.update({
                    "winningNumbers": latest_info['numbers'],
                    "bonus": latest_info['bonus']
                })
            
            doc.reference.update(update_data)
            update_count += 1
            
    print(f"✅ 기존 당첨 내역 {update_count}건 업데이트 완료")

def main():
    print("--- 1. 최신 당첨 정보 동기화 ---")
    latest = get_lotto_data_safe()
    sync_firebase_results(latest)

    print("\n--- 2. 신규 추천 번호 생성 및 업로드 ---")
    next_round = latest['drwNo'] + 1
    
    # 중복 업로드 방지
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재하여 생성을 건너뜁니다.")
        return

    # 알고리즘 기반 번호 생성
    recommendations = generate_advanced_numbers(latest['numbers'])
    best_pick = recommendations[0]
    
    # Firebase 신규 문서 작성 (기존 포맷 복원)
    new_doc = {
        "round": next_round,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": best_pick,
        "full_sets": json.dumps(recommendations),
        "result": "wait",
        "createdAt": dt.now().isoformat(),
        "algorithm": "ColdNumber + HighPriority(32-45) + PatternRemoval"
    }
    
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 추천 번호 업로드 완료: {best_pick}")

if __name__ == "__main__":
    main()
