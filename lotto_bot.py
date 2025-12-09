import firebase_admin
from firebase_admin import credentials, firestore
import requests
import datetime
import random
from collections import Counter
import os

# --- 1. 설정 및 초기화 ---
if os.environ.get('FIREBASE_KEY'):
    cred = credentials.Certificate("serviceAccountKey.json")
else:
    # 로컬 테스트용 (파일 경로 확인 필요)
    cred = credentials.Certificate("serviceAccountKey.json")

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(cred)

db = firestore.client()
COLLECTION_NAME = "lotto_predictions"

# --- 2. 로또 API 및 등수 계산 함수 ---
def get_official_lotto_result(drwNo):
    """동행복권 API에서 특정 회차 결과를 가져옴"""
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
    try:
        res = requests.get(url, timeout=5)
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
        print(f"API Error: {e}")
        return None

def calculate_rank(my_numbers, win_numbers, bonus_number):
    """
    내 번호와 당첨 번호를 비교하여 등수(숫자)와 메시지를 반환
    1등: 6개 일치 / 2등: 5개 + 보너스 / 3등: 5개 / 4등: 4개 / 5등: 3개
    """
    my_set = set(my_numbers)
    win_set = set(win_numbers)
    
    matched_count = len(my_set.intersection(win_set))
    
    if matched_count == 6:
        return 1, "1등"
    elif matched_count == 5 and bonus_number in my_set:
        return 2, "2등"
    elif matched_count == 5:
        return 3, "3등"
    elif matched_count == 4:
        return 4, "4등"
    elif matched_count == 3:
        return 5, "5등"
    else:
        return -1, "낙첨"

# --- 3. 번호 생성 알고리즘 (핵심 로직 유지) ---
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
    # 최신 회차 찾기
    last_drw_no = 1150 
    while True:
        res = get_official_lotto_result(last_drw_no + 1)
        if res: last_drw_no += 1
        else: break
            
    recent_history = []
    for i in range(last_drw_no, last_drw_no - 50, -1):
        res = get_official_lotto_result(i)
        if res: recent_history.append(res)
        
    freq_list = get_cold_numbers_stats(recent_history)
    cold_high = [x[0] for x in freq_list if x[0] >= 32]
    cold_low = [x[0] for x in freq_list if x[0] < 32]
    
    results = []
    # 5세트 생성
    while len(results) < 5:
        pool_high = cold_high[:15]
        pool_low = cold_low[:25]
        
        n_high = random.choice([4, 4, 5, 5, 6])
        n_low = 6 - n_high
        
        try:
            current_high = random.sample(pool_high, n_high)
            current_low = random.sample(pool_low, n_low) if n_low > 0 else []
        except ValueError:
            continue
            
        combination = sorted(current_high + current_low)
        
        if not is_valid_birthday_exclusion(combination): continue
        if has_visual_pattern(combination): continue
        if combination in results: continue
        
        results.append(combination)
        
    return results, last_drw_no

# --- 4. 당첨 확인 로직 (주요 수정 부분) 확인 ---
def check_winning_status():
    """
    Firestore에서 '결과 대기(wait)' 상태인 문서를 찾아
    5개 세트 각각의 당첨 여부를 확인하고 상세 결과를 저장함
    """
    # 결과가 아직 안 나온(wait) 문서 조회
    docs = db.collection(COLLECTION_NAME).where("result", "==", "wait").stream()
    
    updates_made = 0
    for doc in docs:
        data = doc.to_dict()
        round_no = data['round']
        
        # 1. 저장된 5개 세트 가져오기 (없으면 메인 번호 1개만이라도 가져옴)
        my_sets = data.get('full_sets', [data.get('numbers', [])])
        
        # 2. 실제 결과 조회
        official = get_official_lotto_result(round_no)
        if not official:
            print(f"{round_no}회차: 아직 발표 안 됨")
            continue
            
        win_numbers = official['numbers']
        bonus_number = official['bonus']
        
        # 3. 5개 세트 각각 등수 계산
        detailed_results = [] # 상세 결과를 담을 리스트
        best_rank = -1        # 가장 잘한 등수 (메인 상태 표시용)
        is_any_win = False    # 하나라도 당첨됐는지

        for idx, numbers in enumerate(my_sets):
            rank, msg = calculate_rank(numbers, win_numbers, bonus_number)
            
            # 상세 결과 저장 구조
            detailed_results.append({
                "index": idx + 1,      # 1번 세트, 2번 세트...
                "numbers": numbers,    # 해당 세트 번호
                "rank": rank,          # 등수 (1~5, 또는 -1)
                "message": msg         # "5등", "낙첨" 등
            })
            
            # 최고 등수 갱신 (1등이 숫자 1로 가장 작으므로 로직 주의)
            if rank != -1: # 당첨된 경우
                is_any_win = True
                if best_rank == -1 or rank < best_rank:
                    best_rank = rank

        # 4. 전체 결과 상태 결정
        final_status = "win" if is_any_win else "lose"
        
        # 5. Firestore 업데이트
        doc.reference.update({
            "result": final_status,          # 전체 성공/실패 여부
            "best_rank": best_rank,          # 최고 등수 (예: 5)
            "winningNumbers": win_numbers,   # 이번 회차 당첨번호
            "bonus": bonus_number,           # 보너스 번호
            "detailed_results": detailed_results # ★ 5개 세트 각각의 결과 리스트
        })
        
        print(f"✅ {round_no}회차 업데이트 완료: {final_status} (최고 {best_rank if best_rank != -1 else '낙첨'}등)")
        updates_made += 1
        
    if updates_made == 0:
        print("업데이트할 지난 회차 정보가 없습니다.")

# --- 5. 메인 실행 함수 ---
def main():
    print("--- 1. 지난 회차 당첨 여부 확인 ---")
    check_winning_status()
    
    print("\n--- 2. 다음 회차 번호 생성 및 업로드 ---")
    recommendations, last_round = generate_recommendations()
    next_round = last_round + 1
    
    # 중복 체크
    existing = db.collection(COLLECTION_NAME).where("round", "==", next_round).get()
    if len(existing) > 0:
        print(f"⚠️ {next_round}회차 데이터는 이미 존재합니다.")
        return

    # 날짜 계산
    today = datetime.date.today()
    days_ahead = 5 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    next_date = today + datetime.timedelta(days=days_ahead)
    
    new_doc = {
        "round": next_round,
        "drawDate": next_date.strftime("%Y-%m-%d"),
        "numbers": recommendations[0],    # 메인 표시는 첫 번째 세트
        "full_sets": recommendations,     # 5개 세트 전체 저장
        "aiComment": "최근 콜드 넘버와 생일 제외 필터를 적용한 5개 조합입니다.",
        "result": "wait",
        "createdAt": datetime.datetime.now().isoformat()
    }
    
    db.collection(COLLECTION_NAME).add(new_doc)
    print(f"🚀 {next_round}회차 추천 번호 5세트 업로드 완료")

if __name__ == "__main__":
    main()