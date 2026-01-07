import firebase_admin
from firebase_admin import credentials, firestore
import requests
import datetime
from datetime import datetime as dt
import random
from collections import Counter
import os
import json
import time

# --- 1. 설정 및 초기화 ---
if os.environ.get('FIREBASE_KEY'):
    # GitHub Actions 환경에서는 환경 변수에 저장된 JSON 문자열을 파일로 써서 로드하거나 직접 로드 가능
    cred = credentials.Certificate("serviceAccountKey.json")
else:
    cred = credentials.Certificate("serviceAccountKey.json")

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(cred)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. 데이터 로드 및 API 함수 (안정성 강화) ---

def get_official_lotto_result(drwNo):
    """동행복권 API (차단 방지 헤더 및 재시도 로직 강화)"""
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Referer": "https://www.dhlottery.co.kr/"
    }
    
    for attempt in range(3):  # 최대 3회 재시도
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('returnValue') == 'success':
                    return {
                        'drwNo': data['drwNo'],
                        'date': data['drwNoDate'],
                        'numbers': [data[f'drwtNo{i}'] for i in range(1, 7)],
                        'bonus': data['bnusNo']
                    }
            time.sleep(2) # 차단 방지를 위한 간격
        except Exception as e:
            print(f"⚠️ {drwNo}회차 시도 {attempt+1}: {e}")
            time.sleep(2)
    return None

def fetch_history_data():
    """가장 안정적인 GitHub 데이터셋 또는 대체 경로 활용"""
    # 주소 1: 공식 데이터 보존용 (주로 업데이트가 빠름)
    urls = [
        "https://raw.githubusercontent.com/yous/lotto/master/data.json",
        "https://raw.githubusercontent.com/p lottery/lotto-data/master/data.json" # 대안 주소
    ]
    
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                # 데이터가 리스트 형태이고 내부에 회차 정보가 있는지 확인
                if isinstance(data, list) and len(data) > 0:
                    # 최신순 정렬 (데이터 구조에 따라 key는 다를 수 있음)
                    return sorted(data, key=lambda x: x.get('round', x.get('drwNo', 0)), reverse=True)
        except:
            continue
    return []

# --- 3. [요청 반영] 로또 번호 추출 알고리즘 ---

def get_cold_numbers_pool(history_data):
    """최근 5년(260회) 기준 하위 20% Cold Number 추출"""
    all_numbers = []
    target_data = history_data[:260] 
    for record in target_data:
        # 데이터셋마다 필드명이 다를 수 있으므로 예외 처리
        nums = record.get('numbers') or [record.get(f'drwtNo{i}') for i in range(1, 7)]
        all_numbers.extend(nums)
    
    counts = Counter(all_numbers)
    freq_list = [(n, counts.get(n, 0)) for n in range(1, 46)]
    freq_list.sort(key=lambda x: x[1])
    
    cold_pool = [x[0] for x in freq_list[:9]] # 하위 20%
    remaining_pool = [x[0] for x in freq_list[9:]]
    return cold_pool, remaining_pool

def is_valid_selection(numbers):
    """고번호 4개 이상 & 시각 패턴 제거"""
    if sum(1 for n in numbers if 32 <= n <= 45) < 4:
        return False

    grid = [[0]*7 for _ in range(7)]
    for n in numbers:
        r, c = (n - 1) // 7, (n - 1) % 7
        grid[r][c] = 1
    
    for r in range(7):
        for c in range(5):
            if grid[r][c] and grid[r][c+1] and grid[r][c+2]: return False
    for c in range(7):
        for r in range(5):
            if grid[r][c] and grid[r+1][c] and grid[r+2][c]: return False
    return True

def generate_custom_recommendations(history_data):
    cold_pool, remaining_pool = get_cold_numbers_pool(history_data)
    results = []
    while len(results) < 5:
        sample_cold = random.sample(cold_pool, random.randint(1, 2))
        sample_remain = random.sample(remaining_pool, 6 - len(sample_cold))
        combination = sorted(sample_cold + sample_remain)
        if is_valid_selection(combination) and combination not in results:
            results.append(combination)
    return results

# --- 4. 메인 로직 (Firebase 복원 및 실행) ---

def check_winning_status():
    # Positional argument 경고 해결을 위해 'filter' 구조 사용
    from google.cloud.firestore_v1.base_query import FieldFilter
    
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    
    for doc in docs:
        data = doc.to_dict()
        round_no = data['round']
        official = get_official_lotto_result(round_no)
        
        if official:
            # 여기에 기존 등수 계산 알고리즘(calculate_rank)을 추가하여 처리
            doc.reference.update({
                "result": "win/lose_processed",
                "winningNumbers": official['numbers'],
                "bonus": official['bonus'],
                "updatedAt": dt.now().isoformat()
            })
            print(f"✅ {round_no}회차 결과 업데이트 성공")

def main():
    print("--- 1. 기존 당첨 내역 확인 ---")
    check_winning_status()
    
    print("\n--- 2. 데이터 로드 및 추천 생성 ---")
    history = fetch_history_data()
    if not history:
        print("❌ 에러: GitHub 데이터셋에 접근할 수 없습니다. URL을 확인하세요.")
        return

    # 회차 정보 추출 (데이터셋 구조 유연하게 대응)
    last_round = history[0].get('round') or history[0].get('drwNo')
    next_round = last_round + 1
    
    # 중복 확인
    from google.cloud.firestore_v1.base_query import FieldFilter
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 추천이 이미 존재합니다.")
        return

    recommendations = generate_custom_recommendations(history)
    
    new_doc = {
        "round": next_round,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": recommendations[0],
        "full_sets": json.dumps(recommendations),
        "result": "wait",
        "createdAt": dt.now().isoformat()
    }
    
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 업로드 완료 (Cold Number + 고번호 전략)")

if __name__ == "__main__":
    main()
