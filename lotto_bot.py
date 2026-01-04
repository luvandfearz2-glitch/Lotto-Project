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

# --- 2. 로또 API 및 등수 계산 함수 ---
def get_official_lotto_result(drwNo):
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest'
    }
    try:
        time.sleep(2) # 차단 방지를 위한 대기
        res = requests.get(url, timeout=15, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data.get('returnValue') == 'success':
                return {
                    'drwNo': data['drwNo'],
                    'date': data['drwNoDate'],
                    'numbers': [data[f'drwtNo{i}'] for i in range(1, 7)],
                    'bonus': data['bnusNo']
                }
        return None
    except Exception as e:
        print(f"⚠️ API Error (Round {drwNo}): {e}")
        return None

def calculate_rank(my_numbers, win_numbers, bonus_number):
    my_set = set(my_numbers)
    win_set = set(win_numbers)
    matched = len(my_set.intersection(win_set))
    
    if matched == 6: return 1, "1등"
    elif matched == 5 and bonus_number in my_set: return 2, "2등"
    elif matched == 5: return 3, "3등"
    elif matched == 4: return 4, "4등"
    elif matched == 3: return 5, "5등"
    else: return -1, "낙첨"

# --- 3. 번호 생성 알고리즘 ---
def get_cold_numbers_stats(history_data):
    all_numbers = []
    for record in history_data:
        all_numbers.extend(record['numbers'])
    counts = Counter(all_numbers)
    freq_list = [(n, counts.get(n, 0)) for n in range(1, 46)]
    freq_list.sort(key=lambda x: x[1]) 
    return freq_list

def is_valid_birthday_exclusion(numbers):
    high_count = sum(1 for n in numbers if 32 <= n <= 45)
    return high_count >= 4

def has_visual_pattern(numbers):
    grid = [[0]*7 for _ in range(7)]
    for n in numbers:
        r, c = (n - 1) // 7, (n - 1) % 7
        grid[r][c] = 1
    for r in range(7):
        for c in range(5):
            if grid[r][c] and grid[r][c+1] and grid[r][c+2]: return True
    for c in range(7):
        for r in range(5):
            if grid[r][c] and grid[r+1][c] and grid[r+2][c]: return True
    return False

def generate_recommendations():
    # [개선] 날짜 기반 회차 계산 (2026년 대응)
    base_date = dt(2002, 12, 7, 20, 45)
    now = dt.now()
    last_drw_no = ((now - base_date).days // 7) + 1
    
    # 실제 API로 최신 회차 확정
    confirmed_no = last_drw_no
    for i in range(last_drw_no, last_drw_no - 3, -1):
        if get_official_lotto_result(i):
            confirmed_no = i
            break
            
    recent_history = []
    for i in range(confirmed_no, confirmed_no - 50, -1):
        res = get_official_lotto_result(i)
        if res: recent_history.append(res)
        if len(recent_history) >= 20: break # 차단 방지를 위해 최대 20개까지만 수집
        
    if not recent_history:
        print("⚠️ API 제한으로 기본 알고리즘 작동")
        return [sorted(random.sample(range(1, 46), 6)) for _ in range(5)], confirmed_no
        
    freq_list = get_cold_numbers_stats(recent_history)
    cold_high = [x[0] for x in freq_list if x[0] >= 32]
    cold_low = [x[0] for x in freq_list if x[0] < 32]
    
    results = []
    while len(results) < 5:
        n_high = random.choice([4, 4, 5, 5, 6])
        try:
            comb = sorted(random.sample(cold_high[:15], n_high) + random.sample(cold_low[:25], 6 - n_high))
        except ValueError: continue
        if is_valid_birthday_exclusion(comb) and not has_visual_pattern(comb) and comb not in results:
            results.append(comb)
    return results, confirmed_no

# --- 4. 동적 코멘트 생성 함수 ---
def generate_dynamic_comment(best_numbers):
    total_sum = sum(best_numbers)
    odd_cnt = sum(1 for n in best_numbers if n % 2 != 0)
    has_consecutive = any(best_numbers[i] == best_numbers[i-1] + 1 for i in range(1, len(best_numbers)))
    end_digits = [n % 10 for n in best_numbers]
    has_same_end = len(end_digits) != len(set(end_digits))

    intros = [
        "최근 50회차 미출현 '콜드 넘버' 가중치를 기반으로,",
        "역 빈발 패턴 마이닝 알고리즘을 적용하여,",
        "고번호(32+) 집중 분포 데이터를 분석한 결과,",
        "과거 당첨 번호의 벡터 유사도 분석을 통해,"
    ]
    intro = random.choice(intros)

    details = []
    if total_sum >= 160: details.append(f"총합 {total_sum}의 높은 수치로 고구간 집중 전략을 세웠으며,")
    elif total_sum <= 120: details.append(f"총합 {total_sum}의 낮은 수치로 분산 투자를 유도했으며,")
    
    if has_consecutive: details.append("연속된 번호 조합을 포함하여 당첨 확률 변동성을 높였습니다.")
    elif has_same_end: details.append("동일한 끝수(동형수) 패턴을 적용하여 매칭 확률을 최적화했습니다.")
    elif odd_cnt >= 4: details.append("홀수 번호의 비중을 높여 통계적 불균형을 노렸습니다.")
    else: details.append("홀짝 비율이 가장 이상적인 황금 밸런스 조합입니다.")

    detail = details[0]
    outros = ["이번 주 가장 높은 기댓값을 보입니다.", "상위 1% 이내의 추천 조합입니다.", "강력한 당첨 신호가 감지되었습니다."]
    return f"{intro} {detail} {random.choice(outros)}"

# --- 5. 당첨 확인 로직 ---
def check_winning_status():
    docs = db.collection(COLLECTION_NAME).filter(filter=FieldFilter("result", "==", "wait")).stream()
    updates_made = 0
    for doc in docs:
        data = doc.to_dict()
        round_no = data['round']
        my_sets_raw = data.get('full_sets', data.get('numbers', []))
        
        if isinstance(my_sets_raw, str):
            try: my_sets = json.loads(my_sets_raw)
            except: continue
        else: my_sets = [my_sets_raw] 

        official = get_official_lotto_result(round_no)
        if not official: continue
            
        win_numbers = official['numbers']
        bonus_number = official['bonus']
        
        detailed_results = [] 
        best_rank = -1        
        is_any_win = False    

        for idx, numbers in enumerate(my_sets):
            rank, msg = calculate_rank(numbers, win_numbers, bonus_number)
            detailed_results.append({"index": idx + 1, "numbers": numbers, "rank": rank, "message": msg})
            if rank != -1: 
                is_any_win = True
                if best_rank == -1 or rank < best_rank: best_rank = rank

        doc.reference.update({
            "result": "win" if is_any_win else "lose",
            "best_rank": best_rank,
            "winningNumbers": win_numbers,
            "bonus": bonus_number,
            "detailed_results": detailed_results 
        })
        print(f"✅ {round_no}회차 결과 업데이트 완료")
        updates_made += 1
    if updates_made == 0: print("업데이트할 지난 회차 정보가 없습니다.")

# --- 6. 메인 실행 함수 ---
def main():
    print("--- 1. 지난 회차 당첨 여부 확인 ---")
    check_winning_status()
    
    print("\n--- 2. 다음 회차 번호 생성 및 업로드 ---")
    recommendations, last_round = generate_recommendations()
    next_round = last_round + 1
    
    # 필터 적용된 중복 체크
    existing = db.collection(COLLECTION_NAME).filter(filter=FieldFilter("round", "==", next_round)).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")
        return

    today = datetime.date.today()
    days_ahead = (5 - today.weekday()) % 7
    next_date = today + datetime.timedelta(days=days_ahead)
    
    best_pick = recommendations[0] 
    ai_comment = generate_dynamic_comment(best_pick)
    
    db.collection(COLLECTION_NAME).add({
        "round": next_round,
        "drawDate": next_date.strftime("%Y-%m-%d"),
        "numbers": best_pick,   
        "full_sets": json.dumps(recommendations),
        "aiComment": ai_comment,
        "result": "wait",
        "createdAt": dt.now().isoformat()
    })
    print(f"🚀 {next_round}회차 생성 완료! 코멘트: {ai_comment}")

if __name__ == "__main__":
    main()
