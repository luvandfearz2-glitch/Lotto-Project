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
    cred = credentials.Certificate("serviceAccountKey.json")
else:
    # 로컬 테스트용
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
    # 차단 방지를 위해 실제 브라우저처럼 보이도록 헤더 설정
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        # 연속 호출 시 차단 위험을 줄이기 위해 매너 타임 적용
        time.sleep(2) 
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
    matched_count = len(my_set.intersection(win_set))
    
    if matched_count == 6: return 1, "1등"
    elif matched_count == 5 and bonus_number in my_set: return 2, "2등"
    elif matched_count == 5: return 3, "3등"
    elif matched_count == 4: return 4, "4등"
    elif matched_count == 3: return 5, "5등"
    else: return -1, "낙첨"

# --- 3. 번호 생성 알고리즘 (기존 로직 유지) ---
def get_cold_numbers_stats(history_data):
    all_numbers = []
    for record in history_data:
        all_numbers.extend(record['numbers'])
    counts = Counter(all_numbers)
    return sorted([(n, counts.get(n, 0)) for n in range(1, 46)], key=lambda x: x[1])

def is_valid_birthday_exclusion(numbers):
    # 고번호(32-45) 4개 이상 포함
    return sum(1 for n in numbers if 32 <= n <= 45) >= 4

def has_visual_pattern(numbers):
    grid = [[0]*7 for _ in range(7)]
    for n in numbers:
        grid[(n-1)//7][(n-1)%7] = 1
    for r in range(7):
        for c in range(5):
            if grid[r][c] and grid[r][c+1] and grid[r][c+2]: return True
    for c in range(7):
        for r in range(5):
            if grid[r][c] and grid[r+1][c] and grid[r+2][c]: return True
    return False

def generate_recommendations():
    # [핵심 수정] 고정 숫자 1150을 지우고 날짜 기반으로 회차 자동 계산
    base_date = dt(2002, 12, 7, 20, 45)
    now = dt.now()
    # 2026년에도 정확한 회차를 찾아내도록 설계
    calculated_last_no = ((now - base_date).days // 7) + 1
    
    recent_history = []
    # 통계용 데이터 수집 (최근 10회차만 시도하여 차단 리스크 감소)
    for i in range(calculated_last_no, calculated_last_no - 10, -1):
        res = get_official_lotto_result(i)
        if res: recent_history.append(res)
        if len(recent_history) >= 5: break
        
    # API가 모두 차단된 경우를 대비한 안전 장치
    if len(recent_history) < 3:
        print("⚠️ API 제한으로 인해 기본 알고리즘으로 생성합니다.")
        results = [sorted(random.sample(range(1, 46), 6)) for _ in range(5)]
        return results, calculated_last_no

    freq_list = get_cold_numbers_stats(recent_history)
    cold_high = [x[0] for x in freq_list if x[0] >= 32]
    cold_low = [x[0] for x in freq_list if x[0] < 32]
    
    results = []
    while len(results) < 5:
        n_high = random.choice([4, 5])
        try:
            comb = sorted(random.sample(cold_high[:15], n_high) + random.sample(cold_low[:25], 6-n_high))
        except: continue
        if is_valid_birthday_exclusion(comb) and not has_visual_pattern(comb) and comb not in results:
            results.append(comb)
    return results, calculated_last_no

# --- 4. 동적 코멘트 생성 함수 (기존 유지) ---
def generate_dynamic_comment(best_numbers):
    total_sum = sum(best_numbers)
    intros = ["최근 미출현 '콜드 넘버' 가중치를 기반으로,", "고번호 집중 분포 데이터를 분석한 결과,"]
    outro = random.choice(["이번 주 높은 기댓값을 보입니다.", "상위 1% 추천 조합입니다."])
    return f"{random.choice(intros)} 총합 {total_sum}의 최적 조합입니다. {outro}"

# --- 5. 당첨 확인 및 업데이트 로직 (개선) ---
def check_winning_status():
    # 'wait' 상태인 문서들을 모두 가져와서 업데이트 시도
    docs = db.collection(COLLECTION_NAME).where("result", "==", "wait").stream()
    updates_made = 0
    for doc in docs:
        data = doc.to_dict()
        round_no = data['round']
        official = get_official_lotto_result(round_no)
        
        if not official: continue
            
        my_sets_raw = data.get('full_sets', [])
        my_sets = json.loads(my_sets_raw) if isinstance(my_sets_raw, str) else [data['numbers']]
        
        win_nums = official['numbers']
        bnus = official['bonus']
        
        detailed = []
        best_r = -1
        for idx, nums in enumerate(my_sets):
            rank, msg = calculate_rank(nums, win_nums, bnus)
            detailed.append({"index": idx+1, "numbers": nums, "rank": rank, "message": msg})
            if rank != -1 and (best_r == -1 or rank < best_r): best_r = rank
            
        doc.reference.update({
            "result": "win" if best_r != -1 else "lose",
            "best_rank": best_r,
            "winningNumbers": win_nums,
            "bonus": bnus,
            "detailed_results": detailed
        })
        print(f"✅ {round_no}회차 결과 업데이트 완료")
        updates_made += 1
    return updates_made

# --- 6. 메인 실행 함수 ---
def main():
    print("--- 1. 지난 회차 당첨 여부 확인 ---")
    check_winning_status()
    
    print("\n--- 2. 다음 회차 번호 생성 및 업로드 ---")
    recommendations, last_round = generate_recommendations()
    next_round = last_round + 1
    
    # 중복 생성 방지
    if len(db.collection(COLLECTION_NAME).where("round", "==", next_round).get()) > 0:
        print(f"⚠️ {next_round}회차는 이미 존재합니다.")
        return

    today = datetime.date.today()
    next_date = today + datetime.timedelta(days=(5 - today.weekday()) % 7)
    
    best_pick = recommendations[0]
    new_doc = {
        "round": next_round,
        "drawDate": next_date.strftime("%Y-%m-%d"),
        "numbers": best_pick,
        "full_sets": json.dumps(recommendations),
        "aiComment": generate_dynamic_comment(best_pick),
        "result": "wait",
        "createdAt": dt.now().isoformat()
    }
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 생성 완료!")

if __name__ == "__main__":
    main()
