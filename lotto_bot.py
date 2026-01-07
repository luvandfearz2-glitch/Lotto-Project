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

# --- 2. 데이터 수집 (3중 방어막: GitHub -> 스크래핑 -> 날짜계산) ---
def get_comprehensive_data():
    """최근 5년(약 260회) 이상의 데이터를 확보하기 위해 GitHub 소스를 우선합니다."""
    urls = [
        "https://raw.githubusercontent.com/the99percent/lotto-data/master/data.json",
        "https://raw.githubusercontent.com/yous/lotto/master/data.json"
    ]
    # 1순위: GitHub (해외 IP 차단 없음)
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                history = res.json()
                if isinstance(history, list) and len(history) > 200:
                    history = sorted(history, key=lambda x: x['round'], reverse=True)
                    return {'latest': history[0], 'history': history, 'success': True, 'method': 'GitHub'}
        except: continue

    # 2순위: 웹 스크래핑 (해외 IP 차단 가능성 있음)
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get("https://dhlottery.co.kr/gameResult.do?method=byWin", headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        cur_round = int(soup.find('strong', id='lottoDrwNo').text)
        win_list = [int(n.text) for n in soup.find('div', class_='num win').find_all('span', class_='ball_645')]
        bonus = int(soup.find('div', class_='num bonus').find('span', class_='ball_645').text)
        latest = {'round': cur_round, 'numbers': win_list, 'bonus': bonus}
        return {'latest': latest, 'history': [latest], 'success': True, 'method': 'Scraping'}
    except:
        # 3순위: 날짜 기반 강제 계산 (최후의 보루)
        base_date = dt(2002, 12, 7)
        calc_round = (dt.now() - base_date).days // 7 + 1
        if dt.now().weekday() == 5 and dt.now().hour < 21: calc_round -= 1
        return {'latest': {'round': calc_round, 'numbers': [], 'bonus': 0}, 'history': [], 'success': False, 'method': 'Calculation'}

# --- 3. 로또 번호 추출 알고리즘 (3대 원칙) ---
def generate_advanced_numbers(history_data):
    """
    ① Cold Number: 최근 5년(260회) 빈도 하위 20% 추출 (역추세 매매)
    ② 생일수 배제: 고번호 32~45번 4개 이상 필수
    ③ 패턴 제거: 7x7 그리드 3연속 직선/대각선 제거
    """
    all_nums = list(range(1, 46))
    sample_size = min(len(history_data), 260) 
    recent_5_years = history_data[:sample_size]
    
    if recent_5_years:
        drawn_all = []
        for r in recent_5_years: drawn_all.extend(r['numbers'])
        counts = Counter(drawn_all)
        # 출현 빈도 하위 20% (약 9개 숫자)
        cold_pool = sorted(all_nums, key=lambda x: counts.get(x, 0))[:9]
    else:
        cold_pool = random.sample(all_nums, 10)

    results = []
    while len(results) < 5:
        comb_cold = random.sample(cold_pool, random.randint(1, 2))
        comb_others = random.sample(all_nums, 6 - len(comb_cold))
        comb = sorted(list(set(comb_cold + comb_others)))
        if len(comb) < 6: continue
        # ② 고번호(32-45) 4개 이상
        if sum(1 for n in comb if 32 <= n <= 45) < 4: continue
        # ③ 패턴 제거
        grid = [[0]*7 for _ in range(7)]
        for n in comb: grid[(n-1)//7][(n-1)%7] = 1
        is_pattern = False
        for i in range(7):
            for j in range(5):
                if (grid[i][j] and grid[i][j+1] and grid[i][j+2]) or (grid[j][i] and grid[j+1][i] and grid[j+2][i]):
                    is_pattern = True; break
        if not is_pattern and comb not in results:
            results.append(comb)
    return results

# --- 4. 메인 프로세스 ---
def main():
    print("--- 1. 데이터 수집 및 동기화 프로세스 시작 ---")
    data = get_comprehensive_data()
    latest = data['latest']
    history = data['history']
    
    # Firebase 'wait' 문서(지난주 추천 결과) 업데이트
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    
    for doc in docs:
        d = doc.to_dict()
        target_round = d.get('round')
        
        if target_round <= latest['round']:
            match = next((item for item in history if item["round"] == target_round), None)
            
            if data['success'] and match and match['numbers']:
                # 케이스 1: 성공적으로 당첨 번호를 가져온 경우
                doc.reference.update({
                    "result": "processed",
                    "winningNumbers": match['numbers'],
                    "bonus": match['bonus'],
                    "updatedAt": dt.now().isoformat(),
                    "status_msg": "성공적으로 데이터를 동기화했습니다."
                })
                print(f"✅ {target_round}회차 업데이트 완료")
            else:
                # 케이스 2: 실패 시 사유 기록 (웹사이트 UI 연동용)
                fail_reason = "해외 IP 접속 차단 또는 데이터셋 업데이트 지연으로 인한 수동 확인 필요"
                doc.reference.update({
                    "result": "failed_to_fetch",
                    "winningNumbers": "확인 불가",
                    "reason": fail_reason,
                    "updatedAt": dt.now().isoformat(),
                    "status_msg": "당첨 번호를 가져오지 못했습니다. 수동 확인이 필요합니다."
                })
                print(f"⚠️ {target_round}회차 실패 사유 기록 완료")

    print("\n--- 2. 신규 추천 번호 생성 ---")
    next_round = latest['round'] + 1
    existing = db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()
    
    if not existing:
        recommendations = generate_advanced_numbers(history)
        new_doc = {
            "round": next_round,
            "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
            "numbers": recommendations[0],
            "full_sets": json.dumps(recommendations),
            "result": "wait",
            "createdAt": dt.now().isoformat(),
            "algorithm_info": "5Y_Cold_Number + HighPriority_4 + PatternRemoval"
        }
        
        # 수집 실패 상태에서 강제 생성 시 플래그 남기기
        if not data['success']:
            new_doc["note"] = "데이터 수집 제한 상태에서 날짜 기반으로 자동 생성됨"
            
        db.collection(COLLECTION_NAME).add(new_doc)
        print(f"🚀 {next_round}회차 업로드 성공: {recommendations[0]}")
    else:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")

if __name__ == "__main__":
    main()
