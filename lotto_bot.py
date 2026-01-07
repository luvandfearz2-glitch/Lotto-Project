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

# --- 2. 데이터 수집 (웹 스크래핑 방식) ---
def get_lotto_data_from_web():
    """웹사이트 스크래핑을 통해 최신 당첨 번호와 과거 데이터를 시뮬레이션합니다."""
    url = "https://dhlottery.co.kr/gameResult.do?method=byWin"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 최신 회차 및 번호 파싱
        cur_round = int(soup.find('strong', id='lottoDrwNo').text)
        win_list = soup.find('div', class_='num win').find_all('span', class_='ball_645')
        numbers = [int(n.text) for n in win_list]
        bonus = int(soup.find('div', class_='num bonus').find('span', class_='ball_645').text)
        
        return {'drwNo': cur_round, 'numbers': numbers, 'bonus': bonus}
    except Exception as e:
        print(f"❌ 웹 데이터 수집 실패: {e}")
        return None

# --- 3. 로또 번호 추출 알고리즘 구현 ---
def generate_advanced_numbers(latest_win_numbers):
    """
    ① Cold Number (하위 20% 빈도 가중치)
    ② 생일수 배제 (고번호 32~45번 4개 이상)
    ③ 시각적 패턴 제거 (가로/세로 3연속 금지)
    """
    results = []
    
    # [알고리즘 ①] Cold Number 추출 (가상의 최근 빈도 기반 - 최신 당첨번호 제외군 활용)
    all_numbers = list(range(1, 46))
    # 실제 환경에서는 과거 데이터를 누적하여 하위 20%를 산출하나, 
    # 여기서는 역추세 원칙에 따라 최근 당첨되지 않은 번호들에 높은 가중치를 부여합니다.
    cold_pool = [n for n in all_numbers if n not in latest_win_numbers]
    
    while len(results) < 5:
        # 번호 조합 생성
        sample_cold = random.sample(cold_pool, 3) # Cold Number 우선 반영
        sample_others = random.sample(all_numbers, 3)
        comb = sorted(list(set(sample_cold + sample_others)))
        
        if len(comb) < 6: continue

        # [알고리즘 ②] 생일수 배제 (고번호 32~45번 구간 4개 이상 선택)
        high_nums = [n for n in comb if 32 <= n <= 45]
        if len(high_nums) < 4:
            continue

        # [알고리즘 ③] 시각적 패턴 제거 (용지 7x7 배열 기준)
        grid = [[0]*7 for _ in range(7)]
        for n in comb:
            grid[(n-1)//7][(n-1)%7] = 1
        
        is_pattern = False
        for i in range(7):
            for j in range(5):
                # 가로/세로 3개 이상 일직선 체크
                if (grid[i][j] and grid[i][j+1] and grid[i][j+2]) or \
                   (grid[j][i] and grid[j+1][i] and grid[j+2][i]):
                    is_pattern = True
                    break
        
        # 대각선 패턴 (단순 3연속) 추가 체크
        for r in range(5):
            for c in range(5):
                if grid[r][c] and grid[r+1][c+1] and grid[r+2][c+2]: is_pattern = True

        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. 메인 프로세스 (Firebase 복원) ---
def update_past_results(latest_info):
    """기존 대기 중인(wait) 문서에 당첨 결과 작성"""
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    count = 0
    for doc in docs:
        d = doc.to_dict()
        if d['round'] <= latest_info['drwNo']:
            # 현재 회차와 일치할 경우 결과 업데이트
            doc.reference.update({
                "result": "processed",
                "winningNumbers": latest_info['numbers'] if d['round'] == latest_info['drwNo'] else "Check Manual",
                "bonus": latest_info['bonus'] if d['round'] == latest_info['drwNo'] else 0,
                "updatedAt": dt.now().isoformat()
            })
            count += 1
    print(f"✅ 기존 당첨 내역 {count}건 업데이트 완료")

def main():
    print("--- 1. 최신 데이터 수집 및 기존 내역 업데이트 ---")
    latest = get_lotto_data_from_web()
    if not latest: return
    update_past_results(latest)

    print("\n--- 2. 알고리즘 기반 신규 추천 번호 생성 ---")
    next_round = latest['drwNo'] + 1
    
    # 중복 작성 방지
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")
        return

    recommendations = generate_advanced_numbers(latest['numbers'])
    
    # Firebase 신규 문서 작성 (원상태 복원 포맷)
    new_doc = {
        "round": next_round,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": recommendations[0],
        "full_sets": json.dumps(recommendations),
        "result": "wait",
        "createdAt": dt.now().isoformat(),
        "algorithm_info": "ColdNumber-20, BirthExclusion-High4, PatternRemoval-7x7"
    }
    
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 추천 번호 업로드 완료: {recommendations[0]}")

if __name__ == "__main__":
    main()
