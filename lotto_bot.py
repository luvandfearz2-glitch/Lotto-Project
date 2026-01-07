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

# --- 2. 데이터 수집 (최근 5년 데이터 확보를 위한 3중 소스) ---
def get_comprehensive_data():
    """최근 5년(약 260회) 이상의 데이터를 확보하기 위해 GitHub 소스를 우선합니다."""
    urls = [
        "https://raw.githubusercontent.com/the99percent/lotto-data/master/data.json",
        "https://raw.githubusercontent.com/yous/lotto/master/data.json"
    ]
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                history = res.json()
                if isinstance(history, list) and len(history) > 200:
                    history = sorted(history, key=lambda x: x['round'], reverse=True)
                    return {'latest': history[0], 'history': history, 'success': True, 'method': 'GitHub'}
        except: continue

    # 백업: 웹 스크래핑 (최신 회차만 확인 가능)
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
        # 최후 수단: 날짜 계산
        base_date = dt(2002, 12, 7)
        calc_round = (dt.now() - base_date).days // 7 + 1
        if dt.now().weekday() == 5 and dt.now().hour < 21: calc_round -= 1
        return {'latest': {'round': calc_round, 'numbers': [], 'bonus': 0}, 'history': [], 'success': False, 'method': 'Calculation'}

# --- 3. 로또 번호 추출 알고리즘 (3대 원칙) ---
def generate_advanced_numbers(history_data):
    """
    ① Cold Number: 최근 5년(260회) 빈도 하위 20% 우선 추출
    ② 생일수 배제: 고번호 32~45번 4개 이상 필수
    ③ 패턴 제거: 7x7 그리드 3연속 직선/대각선 제거
    """
    all_nums = list(range(1, 46))
    
    # [알고리즘 ①] 5년(260회) 데이터 기반 Cold Number 분석
    sample_size = min(len(history_data), 260) 
    recent_5_years = history_data[:sample_size]
    
    if recent_5_years:
        drawn_all = []
        for r in recent_5_years: drawn_all.extend(r['numbers'])
        counts = Counter(drawn_all)
        # 출현 빈도 하위 20% (약 9개 숫자)를 핵심 후보군으로 설정
        cold_pool = sorted(all_nums, key=lambda x: counts.get(x, 0))[:9]
    else:
        cold_pool = random.sample(all_nums, 10)

    results = []
    while len(results) < 5:
        # Cold Number에서 1~2개 섞고 나머지 랜덤
        comb_cold = random.sample(cold_pool, random.randint(1, 2))
        comb_others = random.sample(all_nums, 6 - len(comb_cold))
        comb = sorted(list(set(comb_cold + comb_others)))
        
        if len(comb) < 6: continue

        # [알고리즘 ②] 생일수 배제 (고번호 32-45번 4개 이상 포함)
        if sum(1 for n in comb if 32 <= n <= 45) < 4: continue

        # [알고리즘 ③] 시각적 패턴 제거 (7x7 그리드)
        grid = [[0]*7 for _ in range(7)]
        for n in comb: grid[(n-1)//7][(n-1)%7] = 1
        
        is_pattern = False
        for i in range(7):
            for j in range(5):
                if (grid[i][j] and grid[i][j+1] and grid[i][j+2]) or \
                   (grid[j][i] and grid[j+1][i] and grid[j+2][i]):
                    is_pattern = True; break
        
        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. 메인 프로세스 (사유 작성 기능 포함) ---
def main():
    print("--- 1. 데이터 수집 및 지난 회차 결과 동기화 ---")
    data = get_comprehensive_data()
    latest = data['latest']
    history = data['history']
    
    # Firebase 'wait' 문서(지난주 추천 결과) 업데이트
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    
    for doc in docs:
        d = doc.to_dict()
        target_round = d.get('round')
        
        if target_round <= latest['round']:
            # 전체 히스토리에서 해당 회차 당첨 정보 검색
            match = next((item for item in history if item["round"] == target_round), None)
            
            if match and match['numbers']:
                # 당첨 번호 획득 성공 시
                doc.reference.update({
                    "result": "processed",
                    "winningNumbers": match['numbers'],
                    "bonus": match['bonus'],
                    "updatedAt": dt.now().isoformat(),
                    "status_msg": "성공적으로 당첨 번호를 가져왔습니다."
                })
                print(f"📝 {target_round}회차 결과 업데이트 완료")
            else:
                # [중요] 당첨 번호 획득 실패 시 사유 입력 (요청 사항)
                fail_reason = "GitHub/웹사이트 접속 실패 (해외 IP 차단 또는 업데이트 지연)"
                doc.reference.update({
                    "result": "failed_to_fetch",
                    "winningNumbers": "확인 불가",
                    "bonus": 0,
                    "reason": fail_reason,
                    "updatedAt": dt.now().isoformat(),
                    "status_msg": "당첨 번호를 가져오지 못했습니다. 수동 확인이 필요합니다."
                })
                print(f"⚠️ {target_round}회차 당첨 정보 획득 실패 사유 기록 완료")

    print("\n--- 2. 신규 추천 번호 생성 및 업로드 ---")
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
            "algorithm_info": "5Y_Cold_Number + BirthDay_Exclude + No_Pattern"
        }
        db.collection(COLLECTION_NAME).add(new_doc)
        print(f"🚀 {next_round}회차 업로드 성공: {recommendations[0]}")
    else:
        print(f"⚠️ {next_round}회차 데이터가 이미 존재합니다.")

if __name__ == "__main__":
    main()
