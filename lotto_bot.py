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
    print("❌ serviceAccountKey.json 파일을 찾을 수 없습니다.")
    exit(1)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. 데이터 수집 (GitHub 이중 소스 + 웹 스크래핑 백업) ---
def get_lotto_history():
    """안정적인 GitHub 소스를 우선 사용하고 실패 시 웹 스크래핑을 시도합니다."""
    urls = [
        "https://raw.githubusercontent.com/the99percent/lotto-data/master/data.json",
        "https://raw.githubusercontent.com/yous/lotto/master/data.json"
    ]
    
    # 1단계: GitHub 데이터셋 시도
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                history = res.json()
                if isinstance(history, list) and len(history) > 0:
                    # 회차 기준 내림차순 정렬 (최신이 0번 인덱스)
                    history = sorted(history, key=lambda x: x['round'], reverse=True)
                    return {'latest': history[0], 'history': history, 'success': True}
        except: continue

    # 2단계: 백업 웹 스크래핑 (최신 정보만)
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get("https://dhlottery.co.kr/gameResult.do?method=byWin", headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        cur_round = int(soup.find('strong', id='lottoDrwNo').text)
        win_list = [int(n.text) for n in soup.find('div', class_='num win').find_all('span', class_='ball_645')]
        bonus = int(soup.find('div', class_='num bonus').find('span', class_='ball_645').text)
        latest = {'round': cur_round, 'numbers': win_list, 'bonus': bonus}
        return {'latest': latest, 'history': [latest], 'success': True}
    except:
        return {'success': False}

# --- 3. 로또 번호 추출 알고리즘 (3대 원칙) ---
def generate_advanced_numbers(history_data):
    """
    ① Cold Number (최근 100회 빈도 하위 20%)
    ② 생일수 배제 (고번호 32-45번 4개 이상)
    ③ 패턴 제거 (7x7 그리드 3연속 금지)
    """
    # 통계 분석 (최근 100회차 기준)
    recent = history_data[:100]
    all_drawn = []
    for r in recent: all_drawn.extend(r['numbers'])
    
    counts = Counter(all_drawn)
    # 빈도 하위 20% (약 9개 번호)를 Cold Pool로 설정
    cold_pool = sorted(range(1, 46), key=lambda x: counts.get(x, 0))[:9]
    normal_pool = list(range(1, 46))

    results = []
    while len(results) < 5:
        # Cold Number 1~2개 포함 + 나머지 랜덤
        comb = random.sample(cold_pool, random.randint(1, 2))
        comb += random.sample(normal_pool, 6 - len(comb))
        comb = sorted(list(set(comb)))
        
        if len(comb) < 6: continue

        # [원칙 ②] 고번호(32-45) 4개 이상 포함
        if sum(1 for n in comb if 32 <= n <= 45) < 4: continue

        # [원칙 ③] 시각적 패턴 제거 (7x7 그리드)
        grid = [[0]*7 for _ in range(7)]
        for n in comb: grid[(n-1)//7][(n-1)%7] = 1
        
        is_pattern = False
        for i in range(7):
            for j in range(5):
                # 가로/세로 3연속 체크
                if (grid[i][j] and grid[i][j+1] and grid[i][j+2]) or \
                   (grid[j][i] and grid[j+1][i] and grid[j+2][i]):
                    is_pattern = True; break
            if is_pattern: break

        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. 메인 실행 프로세스 (Firebase 동기화 및 업로드) ---
def main():
    print("--- 1. 데이터 수집 및 지난 회차 업데이트 ---")
    data = get_lotto_history()
    if not data['success']:
        print("🛑 모든 데이터 소스 접근 실패")
        return

    latest = data['latest']
    history = data['history']
    print(f"✅ 최신 데이터 확인: {latest['round']}회")

    # [복원 기능] Firebase 'wait' 상태 문서 당첨 결과 업데이트
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    upd_count = 0
    for doc in docs:
        d = doc.to_dict()
        target_round = d.get('round')
        # 데이터셋에서 해당 회차 정보 찾기
        match = next((item for item in history if item["round"] == target_round), None)
        
        if match:
            doc.reference.update({
                "result": "processed",
                "winningNumbers": match['numbers'],
                "bonus": match['bonus'],
                "updatedAt": dt.now().isoformat()
            })
            upd_count += 1
            print(f"📝 {target_round}회차 결과 동기화 완료")

    print("\n--- 2. 다음 회차 번호 생성 및 업로드 ---")
    next_round = latest['round'] + 1
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    
    if not existing:
        # 최신 데이터를 통계에 반영하여 번호 생성
        recs = generate_advanced_numbers(history)
        new_doc = {
            "round": next_round,
            "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
            "numbers": recs[0], # 대표 번호
            "full_sets": json.dumps(recs), # 전체 5세트
            "result": "wait",
            "createdAt": dt.now().isoformat(),
            "algorithm": "ColdNumber_V1 + HighPriority_4 + PatternRemoval"
        }
        db.collection(COLLECTION_NAME).add(new_doc)
        print(f"🚀 {next_round}회차 추천 업로드 성공: {recs[0]}")
    else:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")

if __name__ == "__main__":
    main()
