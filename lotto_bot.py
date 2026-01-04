import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
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
    cred = credentials.Certificate("serviceAccountKey.json")
else:
    cred = credentials.Certificate("serviceAccountKey.json")

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(cred)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. 로또 API 및 등수 계산 함수 (보안 강화) ---
def get_official_lotto_result(drwNo):
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest'
    }
    try:
        time.sleep(3) # 차단 방지를 위해 대기 시간을 더 늘림
        res = requests.get(url, timeout=20, headers=headers)
        if res.status_code == 200:
            try:
                data = res.json()
                if data.get('returnValue') == 'success':
                    return {
                        'drwNo': data['drwNo'],
                        'numbers': [data[f'drwtNo{i}'] for i in range(1, 7)],
                        'bonus': data['bnusNo']
                    }
            except json.JSONDecodeError:
                print(f"⚠️ {drwNo}회차: JSON이 아닌 응답이 왔습니다. (차단 가능성)")
        return None
    except Exception as e:
        print(f"⚠️ API Error (Round {drwNo}): {e}")
        return None

def calculate_rank(my_numbers, win_numbers, bonus_number):
    my_set, win_set = set(my_numbers), set(win_numbers)
    matched = len(my_set.intersection(win_set))
    if matched == 6: return 1, "1등"
    elif matched == 5 and bonus_number in my_set: return 2, "2등"
    elif matched == 5: return 3, "3등"
    elif matched count == 4: return 4, "4등"
    elif matched count == 3: return 5, "5등"
    else: return -1, "낙첨"

# --- 3. 번호 생성 및 코멘트 로직 (기존 유지) ---
def get_cold_numbers_stats(history_data):
    all_numbers = []
    for record in history_data:
        all_numbers.extend(record['numbers'])
    counts = Counter(all_numbers)
    return sorted([(n, counts.get(n, 0)) for n in range(1, 46)], key=lambda x: x[1])

def generate_recommendations():
    base_date = dt(2002, 12, 7, 20, 45)
    calc_no = ((dt.now() - base_date).days // 7) + 1
    recent_history = []
    # API 차단 상황을 고려하여 최근 3회차만 시도
    for i in range(calc_no, calc_no - 3, -1):
        res = get_official_lotto_result(i)
        if res: recent_history.append(res)
    
    if len(recent_history) < 2:
        print("⚠️ API 제한으로 기본 알고리즘 생성")
        return [sorted(random.sample(range(1, 46), 6)) for _ in range(5)], calc_no

    freq = get_cold_numbers_stats(recent_history)
    high, low = [x[0] for x in freq if x[0] >= 32], [x[0] for x in freq if x[0] < 32]
    results = []
    while len(results) < 5:
        n_h = random.choice([4, 5])
        comb = sorted(random.sample(high[:15], n_h) + random.sample(low[:25], 6-n_h))
        if comb not in results: results.append(comb)
    return results, calc_no

def generate_dynamic_comment(best_numbers):
    total_sum = sum(best_numbers)
    return f"데이터 분석 결과 총합 {total_sum}의 최적 조합을 도출했습니다. 이번 주 높은 기댓값을 보입니다."

# --- 4. 당첨 업데이트 로직 (필터링 방식 개선) ---
def check_winning_status():
    # Firestore 경고 해결을 위한 FieldFilter 사용
    docs = db.collection(COLLECTION_NAME).filter(filter=FieldFilter("result", "==", "wait")).stream()
    updates = 0
    for doc in docs:
        data = doc.to_dict()
        official = get_official_lotto_result(data['round'])
        if not official: continue
        
        my_sets = json.loads(data['full_sets']) if isinstance(data.get('full_sets'), str) else [data['numbers']]
        best_r = -1
        detailed = []
        for idx, nums in enumerate(my_sets):
            rank, msg = calculate_rank(nums, official['numbers'], official['bonus'])
            detailed.append({"index": idx+1, "numbers": nums, "rank": rank, "message": msg})
            if rank != -1 and (best_r == -1 or rank < best_r): best_r = rank
            
        doc.reference.update({
            "result": "win" if best_r != -1 else "lose",
            "best_rank": best_r,
            "winningNumbers": official['numbers'],
            "bonus": official['bonus'],
            "detailed_results": detailed
        })
        updates += 1
    print(f"✅ 당첨 업데이트 완료: {updates}건")

def main():
    print("--- 1. 당첨 여부 업데이트 ---")
    check_winning_status()
    print("\n--- 2. 신규 번호 생성 ---")
    recoms, last_no = generate_recommendations()
    next_no = last_no + 1
    if db.collection(COLLECTION_NAME).filter(filter=FieldFilter("round", "==", next_no)).get():
        print(f"⚠️ {next_no}회차 이미 존재")
        return
    db.collection(COLLECTION_NAME).add({
        "round": next_no,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": recoms[0],
        "full_sets": json.dumps(recoms),
        "aiComment": generate_dynamic_comment(recoms[0]),
        "result": "wait",
        "createdAt": dt.now().isoformat()
    })
    print(f"🚀 {next_no}회차 생성 완료!")

if __name__ == "__main__":
    main()
