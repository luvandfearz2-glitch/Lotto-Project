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
from google.cloud.firestore_v1.base_query import FieldFilter

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

# --- 2. [수정] 데이터 로드 함수 (현재 작동 확인된 소스) ---

def fetch_history_data():
    """
    현재 유효한 오픈소스 로또 데이터셋 주소들입니다.
    하나가 막히면 다음 주소를 시도합니다.
    """
    urls = [
        # 1. 1회부터 최신 회차까지 잘 관리되는 소스
        "https://raw.githubusercontent.com/yous/lotto/master/data.json",
        # 2. 대체 소스 (구조가 다를 수 있음)
        "https://raw.githubusercontent.com/skylertaylor/lotto-results/master/results.json"
    ]
    
    for url in urls:
        try:
            print(f"🌐 데이터 로드 시도 중: {url}")
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                # 'yous' 저장소 데이터 구조 대응
                if isinstance(data, list):
                    # 최신순 정렬 (회차 번호 기준)
                    return sorted(data, key=lambda x: int(x.get('round', x.get('drwNo', 0))), reverse=True)
                # 객체 형태일 경우
                elif isinstance(data, dict):
                    return sorted(data.values(), key=lambda x: int(x.get('round', x.get('drwNo', 0))), reverse=True)
        except Exception as e:
            print(f"❌ {url} 접속 실패: {e}")
            continue
    return []

def get_official_lotto_result(drwNo):
    """
    동행복권 API 직접 호출 (헤더 강화형)
    """
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={drwNo}"
    # 실제 브라우저와 거의 동일한 헤더 구성
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.dhlottery.co.kr/gameResult.do?method=byWin",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    try:
        # 동행복권은 단순 GET보다 세션 유지가 안전합니다.
        session = requests.Session()
        session.get("https://www.dhlottery.co.kr/", headers=headers, timeout=5)
        response = session.get(url, headers=headers, timeout=5)
        
        # 만약 여전히 403이나 JSON Decode 에러가 나면 텍스트를 확인합니다.
        if response.status_code == 200:
            try:
                return response.json()
            except:
                print(f"⚠️ {drwNo}회차: JSON 파싱 실패 (HTML이 반환되었을 수 있음)")
    except Exception as e:
        print(f"⚠️ {drwNo}회차 API 호출 오류: {e}")
    return None

# --- 3. [복원] 로또 번호 추출 알고리즘 (요청하신 조건 준수) ---

def generate_custom_recommendations(history_data):
    # 최근 5년(260회차) 데이터 추출
    # 데이터셋에 따라 필드명이 'numbers' 또는 'drwtNo1~6'일 수 있음
    all_numbers = []
    for record in history_data[:260]:
        if 'numbers' in record:
            all_numbers.extend(record['numbers'])
        else:
            nums = [record.get(f'drwtNo{i}') for i in range(1, 7)]
            all_numbers.extend([n for n in nums if n])

    # 1. Cold Number 추출 (빈도 하위 20%)
    counts = Counter(all_numbers)
    freq_list = sorted([(n, counts.get(n, 0)) for n in range(1, 46)], key=lambda x: x[1])
    cold_pool = [x[0] for x in freq_list[:9]]
    remaining_pool = [x[0] for x in freq_list[9:]]

    results = []
    while len(results) < 5:
        # Cold Number에서 1~2개 선택
        sample_cold = random.sample(cold_pool, random.randint(1, 2))
        sample_remain = random.sample(remaining_pool, 6 - len(sample_cold))
        comb = sorted(sample_cold + sample_remain)

        # 2. 생일수 배제 (32~45번 고번호가 4개 이상)
        if sum(1 for n in comb if n >= 32) < 4:
            continue

        # 3. 용지 시각적 패턴 제거 (가로/세로 3연속 금지)
        grid = [[0]*7 for _ in range(7)]
        for n in comb:
            grid[(n-1)//7][(n-1)%7] = 1
        
        is_pattern = False
        for i in range(7):
            for j in range(5):
                if grid[i][j] and grid[i][j+1] and grid[i][j+2]: is_pattern = True
                if grid[j][i] and grid[j+1][i] and grid[j+2][i]: is_pattern = True
        
        if not is_pattern and comb not in results:
            results.append(comb)
            
    return results

# --- 4. 메인 실행 로직 ---

def main():
    print("--- 1. 기존 당첨 내역 확인 ---")
    docs = db.collection(COLLECTION_NAME).where(filter=FieldFilter("result", "==", "wait")).stream()
    for doc in docs:
        round_no = doc.to_dict()['round']
        res = get_official_lotto_result(round_no)
        if res and res.get('returnValue') == 'success':
            doc.reference.update({
                "result": "processed",
                "winningNumbers": [res[f'drwtNo{i}'] for i in range(1, 7)],
                "bonus": res['bnusNo'],
                "updatedAt": dt.now().isoformat()
            })
            print(f"✅ {round_no}회차 결과 업데이트 완료")

    print("\n--- 2. 신규 번호 생성 및 업로드 ---")
    history = fetch_history_data()
    if not history:
        print("🛑 데이터를 불러올 수 없습니다. GitHub 소스를 확인하세요.")
        return

    # 최신 회차 번호 추출
    last_round = int(history[0].get('round', history[0].get('drwNo', 0)))
    next_round = last_round + 1
    
    # 중복 체크
    if len(db.collection(COLLECTION_NAME).where(filter=FieldFilter("round", "==", next_round)).get()) > 0:
        print(f"⚠️ {next_round}회차 추천 번호가 이미 존재합니다.")
        return

    recommendations = generate_custom_recommendations(history)
    
    db.collection(COLLECTION_NAME).add({
        "round": next_round,
        "drawDate": (dt.now() + datetime.timedelta(days=(5-dt.now().weekday())%7)).strftime("%Y-%m-%d"),
        "numbers": recommendations[0],
        "full_sets": json.dumps(recommendations),
        "result": "wait",
        "createdAt": dt.now().isoformat()
    })
    print(f"🚀 {next_round}회차 업로드 완료!")

if __name__ == "__main__":
    main()
