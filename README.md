# Kickoff Desk

EPL(PL), 라리가(PD), UEFA 챔피언스리그(CL) 일정을 한국시간으로 모아 보는 Windows용 독립형 프로그램입니다. Python과 PySide6로 만든 네이티브 데스크톱 GUI이며, 브라우저나 웹 서버가 필요하지 않습니다. 경기 결과와 스코어는 제작 의도에 따라 표시하거나 캐시에 저장하지 않습니다.

## 1차 구현 범위

- 달력에서 날짜를 선택해 세 대회의 통합 경기 목록 확인
- `Asia/Seoul` 기준 날짜·시간, 대회 필터, 관심 팀 필터
- 경기 목록과 관심 팀 목록에서 팀 이름 왼쪽에 로고 표시
- 실제 API 팀 이름의 한글 표시, 원문 이름 툴팁
- 경기 상태와 미확정 시간 표시, 날짜가 없는 경기는 별도 목록 표시
- 새로고침, 마지막 성공 갱신 시간, 캐시·오프라인 상태 표시
- 월별 로컬 캐시와 인터넷·인증·요청 제한·응답 오류 처리
- API 키 등록·변경·삭제, Windows 자격 증명 관리자 저장 또는 실행 중에만 사용
- 실제 API 키 없이 실행 가능한 Mock 모드와 자동 테스트

Mock 일정은 실제 경기 일정이 아닙니다. 첫 실행은 Mock 모드이며, 이후에는 저장된 모드 설정을 사용합니다. API는 [football-data.org v4](https://docs.football-data.org/general/v4/index.html)를 사용하며, 계정의 대회 접근 권한과 제공 데이터에 따라 조회 범위가 달라질 수 있습니다.

## 설치와 실행

### Windows 실행 파일로 사용

GitHub Actions의 성공한 **Windows build** 실행에서 `KickoffDesk-windows-x64` 아티팩트를 내려받아 압축을 풀고 `KickoffDesk.exe`를 실행하세요. Python이나 Git을 설치하지 않아도 사용할 수 있습니다. 현재 Actions 아티팩트 보관 기간은 14일입니다.

첫 실행은 Mock 데모 모드입니다. 실제 일정은 **API 키 설정**에서 사용자 키를 등록한 뒤 **Mock 데모 모드**를 해제해 조회합니다. 실행 파일에는 API 키가 들어 있지 않으며 스코어는 표시하지 않습니다.

### 소스에서 개발·실행

개발에는 Python 3.12를 권장합니다. 지원 범위는 Python 3.11 이상입니다. Windows PowerShell에서 저장소를 내려받은 뒤 다음 명령을 실행합니다.

```powershell
cd kickoff-desk
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,build]"
.\.venv\Scripts\python.exe -m kickoff_desk --mock
```

설치된 `kickoff-desk` 명령으로도 실행할 수 있습니다. `--mock`과 `--live`는 이번 실행의 시작 모드를 명시합니다. 인자를 생략하면 저장된 설정을 사용합니다.

```powershell
# 실제 API 모드로 시작: 먼저 프로그램 설정에서 키를 등록하세요.
.\.venv\Scripts\python.exe -m kickoff_desk --live
```

### API 키 관리

프로그램의 설정 화면에서 키를 입력하고 저장하거나 삭제할 수 있습니다. 키 입력란에는 기존 키를 다시 표시하지 않습니다.

- Windows에서 영구 저장을 선택하면 Windows 자격 증명 관리자에 저장합니다. 일반 설정 파일이나 캐시에는 기록하지 않습니다.
- 영구 저장을 선택하지 않으면 이번 실행의 메모리에만 보관합니다. Windows 이외의 환경이나 자격 증명 관리자를 이용할 수 없는 환경에서도 이 방식을 사용할 수 있습니다.
- `FOOTBALL_DATA_API_KEY` 환경변수도 지원합니다. 직접 등록한 키가 우선하며, 설정에서 환경변수 사용을 다시 선택할 수 있습니다.
- 키를 직접 등록하거나 삭제하면 환경변수 자동 사용이 해제됩니다. 따라서 삭제한 직후 환경변수의 키가 뜻하지 않게 다시 사용되지 않습니다. 환경변수 자체를 삭제하는 기능은 아닙니다.

환경변수를 사용하는 경우 프로그램을 실행할 프로세스에 `FOOTBALL_DATA_API_KEY`를 설정하세요. `.env.example`은 변수 이름을 설명하는 예시이며, 프로그램이 `.env` 파일을 자동으로 읽지는 않습니다. 실제 키를 소스, 셸 명령 이력, 로그, GitHub Secrets, 빌드 설정 또는 배포 파일에 넣지 마세요. 빌드에는 키가 필요하지 않습니다.

## 화면 사용 방법

1. 달력에서 날짜를 선택합니다. 해당 월이 처음 열리면 월 전체의 일정을 불러옵니다.
2. 대회 필터를 선택합니다. 관심 팀 설정에는 불러온 경기의 팀과 이미 저장한 관심 팀이 표시됩니다.
3. 관심 팀을 지정한 뒤 관심 팀 필터를 켜면 홈·원정 중 한 팀이라도 일치하는 경기를 표시합니다. 대회 필터와 함께 적용됩니다.
4. 새로고침으로 최신 일정을 요청합니다. 일정을 불러오는 중에도 창이 응답하도록 API 작업을 별도 스레드에서 실행합니다.
5. 경기 상태, 미확정 안내, 마지막 성공 갱신 시간과 캐시 표시를 함께 확인합니다.

`SCHEDULED`는 시간이 확정되지 않은 일정으로 표시합니다. `TIMED`는 확정된 킥오프 시간입니다. 연기·취소·중단 등의 상태도 구분하며, 경기 종료 상태는 표시하지만 스코어는 표시하지 않습니다. 날짜 자체가 없는 경기는 특정 날짜에 임의 배정하지 않고 미정 목록에 표시합니다. 날짜 범위 API가 반환하지 않은 미정 경기를 별도로 검색하지는 않습니다.

### 관심 팀 저장과 복원

**관심 팀 관리 → 팀 선택 → 저장**을 누르면 선택한 팀의 ID와 이름이 저장되고, 프로그램을 종료한 뒤 다시 실행해도 자동으로 불러옵니다. 조회한 달에 경기가 없는 저장된 팀도 관심 팀 관리 목록에 유지됩니다. 팀을 모두 해제하고 저장하면 빈 목록이 유지됩니다. **관심 팀 경기만 보기** 체크 상태는 별도로 저장하지 않으므로 필요할 때 다시 켜세요.

Windows 설정 파일은 `%LOCALAPPDATA%\KickoffDesk\settings.json`입니다. 같은 Windows 사용자 계정에서는 실행 파일을 다른 폴더로 옮기거나 교체해도 이 설정을 사용합니다. 다른 PC로 관심 팀을 옮길 때는 두 프로그램을 종료하고 이 설정 파일을 대상 PC의 같은 앱 데이터 위치에 복사할 수 있습니다. API 키는 이 파일에 없으므로 다른 PC에서는 별도로 등록해야 합니다.

### 팀 로고

경기 목록의 홈·원정 팀과 관심 팀 관리 목록에서 팀 이름 왼쪽에 로고를 표시합니다. 다운로드 중이거나 실패해도 팀 이름은 유지하며, 사용할 수 있는 내장·캐시 로고가 없을 때는 중립 아이콘을 표시합니다.

Mock 모드에는 아스널, 맨체스터 시티, 토트넘, 바르셀로나, 레알 마드리드, 바이에른 뮌헨 6개 팀의 PNG 로고가 내장되어 인터넷 없이 표시됩니다. 내장된 팀 수는 실제 일정에서 지원하는 팀의 제한이 아닙니다. 실제 API 모드에서는 경기 응답의 팀별 `crest` URL로 이미지를 비동기 다운로드하고, 성공한 이미지를 앱 데이터 폴더의 `crests/{팀 ID}.png`에 저장합니다. 이미지 요청에는 API 키나 `X-Auth-Token`을 포함하지 않습니다.

한 번 저장한 로고와 내장 로고는 오프라인에서도 사용할 수 있습니다. 저장되지 않은 팀의 로고를 새로 받으려면 인터넷 연결이 필요합니다. Windows 로고 캐시는 `%LOCALAPPDATA%\KickoffDesk\crests`에 있으므로 실행 파일을 교체해도 유지됩니다. 내장 이미지의 원본 출처와 권리 안내는 [로고 출처 문서](src/kickoff_desk/assets/crests/SOURCES.md)를 확인하세요.

### 팀 이름 한글 표시

실제 API의 `Arsenal FC`, `Tottenham Hotspur FC` 등은 경기 목록과 관심 팀 관리에서 `아스널`, `토트넘`처럼 표시합니다. 이전에 영문으로 저장한 관심 팀도 표시할 때 한글 이름을 적용합니다. 팀 이름 위에 마우스를 올리면 원문을 볼 수 있으며, 경기 데이터와 캐시는 API 원문을 보존합니다.

`teams.py`에 150개 클럽의 정식명·약칭 390개를 등록했습니다. 현재 시즌 참가팀 목록이나 지원 팀 수의 제한은 아닙니다. 이미 한글인 이름과 아직 등록되지 않은 팀 이름은 그대로 표시합니다. 새로운 표기는 전체 이름을 명시적으로 추가하며, 비슷한 이름의 다른 구단이나 유소년 팀을 부분 문자열로 추정하지 않습니다.

### 캐시와 오프라인 조회

Windows에서는 `%LOCALAPPDATA%\KickoffDesk`에 설정과 월별 캐시를 저장합니다. 개발용 Linux에서는 `$XDG_DATA_HOME/kickoff-desk` 또는 `~/.local/share/kickoff-desk`를 사용합니다. Mock 캐시와 실제 API 캐시는 분리합니다.

이미 저장된 달은 캐시를 먼저 표시합니다. 새로고침 실패 시 기존 캐시와 마지막 성공 갱신 시각을 유지하고 오류 안내를 표시합니다. 한 번도 불러오지 않은 달은 오프라인에서 조회할 수 없습니다. 캐시가 오래되었을 수 있으므로 최신 일정은 연결 후 새로고침으로 확인하세요. 캐시 저장 실패도 화면에 안내하며, 불러온 일정은 현재 실행에서 계속 표시합니다.

## API 처리와 요청 제한

요청은 `https://api.football-data.org/v4/competitions/{code}/matches`로 보내며 인증은 `X-Auth-Token` 헤더로만 전달합니다. 월 전체 조회는 대회별 1회씩, 정상 완료 시 총 3회의 요청을 사용합니다. 한국시간의 월 시작·끝을 포함하도록 UTC 조회 날짜를 확장하고, 받은 데이터를 한국시간 기준으로 다시 필터링합니다.

`X-Requests-Available-Minute`, `X-RequestCounter-Reset`, `Retry-After` 응답 헤더와 HTTP 429를 이용해 다음 요청까지 대기 시간을 적용합니다. 대기 중 새로고침은 추가 요청 없이 남은 시간을 안내합니다. 제한 정보가 없는 429 응답은 60초를 기본 대기 시간으로 사용합니다. 무한 재시도나 자동 폴링은 하지 않습니다.

요청에는 연결·응답 제한 시간을 지정하고 리다이렉트를 따르지 않습니다. 일부 대회만 성공한 경우 불완전한 결과로 기존 캐시를 덮어쓰지 않습니다. 서버의 원문 오류나 요청 헤더를 로그·화면에 출력하지 않습니다. API가 반환하는 `score` 필드는 도메인 모델로 가져오지 않습니다.

## 프로젝트 구조

```text
src/kickoff_desk/
  app.py                 실행 옵션과 QApplication 시작
  models.py              경기 모델, 한국시간 변환, 필터
  api.py                 football-data.org v4 통신과 요청 제한
  mock.py                키가 필요 없는 예시 경기 제공
  cache.py               점수·키가 없는 월별 캐시
  service.py             월별 조회와 오프라인 대체 처리
  settings.py            설정과 안전한 키 관리
  logos.py               팀 로고 다운로드와 로컬 이미지 캐시
  teams.py               팀 이름의 한글 표시와 원문 대체
  assets/crests/         Mock 팀 로고 6개와 원본 출처 문서
  ui.py                  PySide6 화면과 백그라운드 작업
tests/                   API Mock, 모델, 캐시, 설정, GUI 테스트
scripts/launcher.py      PyInstaller 진입점
scripts/smoke_app.py     제한 시간 내 Mock GUI 실행 확인
kickoff-desk.spec        Windows 단일 실행 파일 패키징 설정
.github/workflows/
  windows-build.yml      Windows 테스트·빌드·실행 확인
```

API·모델·캐시·서비스는 GUI와 분리되어 테스트용 제공자와 가짜 응답을 주입할 수 있습니다. 새로운 기능을 추가할 때는 이 계층의 동작을 먼저 검증하고 화면에 연결하세요.

## 개발과 테스트

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/smoke_app.py
```

테스트는 실제 API를 호출하지 않습니다. GUI 테스트는 `pytest-qt`를 사용합니다. 디스플레이가 없는 Linux 개발 환경에서는 `QT_QPA_PLATFORM=offscreen`으로 실행하세요. `scripts/smoke_app.py`는 Linux에서 이를 기본 설정하고, 키 환경변수를 제거하고 임시 데이터 폴더에서 Mock GUI를 실행합니다. 기본 제한 시간은 30초입니다.

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
python scripts/smoke_app.py
```

`--smoke-test`는 자동 검사 전용 옵션으로, Mock 모드에서 창을 생성하고 초기 일정을 불러온 뒤 자동 종료합니다. 이 검사는 창 생성·데이터 연결을 확인하며 실제 사용자 조작이나 Windows 화면 배치의 수동 검증을 대신하지 않습니다.

## Windows 실행 파일 빌드

Windows에서 빌드해야 Windows `.exe`가 만들어집니다. Linux에서 Windows로 교차 빌드하지 않습니다.

```powershell
Remove-Item Env:FOOTBALL_DATA_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm kickoff-desk.spec
.\.venv\Scripts\python.exe scripts/smoke_app.py --executable dist/KickoffDesk.exe
```

결과는 `dist/KickoffDesk.exe`입니다. Python을 별도 설치하지 않은 Windows에서 실행하는 단일 파일 배포를 목표로 하며, 콘솔 창은 표시하지 않습니다. 시간대 데이터인 `tzdata`와 PySide6 의존성, 명시한 Mock 로고 PNG 6개 및 출처 문서를 포함합니다. Python wheel에도 같은 로고 파일을 포함합니다. 저장소 전체나 `.env`, 로컬 설정, 다운로드한 로고 캐시, API 키는 빌드 입력에 포함하지 않습니다.

GitHub Actions의 **Windows build** 워크플로는 push, pull request, 수동 실행 시 Python 3.12에서 린트, 데이터 처리 테스트, 소스 GUI 실행, 시작·종료/로고/GUI 테스트, PyInstaller 빌드, 실제 `.exe` Mock 실행을 확인합니다. 테스트는 네 그룹으로 분리해 멈춘 기능을 식별하며, 각 테스트에 30초 제한과 스레드 진단 출력을 적용합니다. 모든 단계에서 API 키를 비우며 Secrets를 참조하지 않습니다. 성공하면 Actions 실행의 `KickoffDesk-windows-x64` 아티팩트에서 실행 파일을 내려받을 수 있습니다. 빌드 결과의 자동 릴리스 게시와 코드 서명은 포함하지 않습니다.

## 검증 범위

2026-10-21 Linux 개발 환경에서 로고와 팀 이름 한글화를 포함한 `pytest -q --timeout=30 --timeout-method=thread` **266개 통과**, `ruff check .`, `ruff format --check .`, `git diff --check` 통과를 확인했습니다. GUI 검증은 실제 Qt 위젯을 사용하는 offscreen 방식이며, 소스 GUI 스크린샷에서 경기 목록과 관심 팀 목록의 로고 표시를 확인했습니다. 소스 Mock 실행 검사도 모든 예시 팀의 로고가 중립 아이콘과 구분되는지 확인하고 통과했습니다.

자동 테스트와 Mock 실행 검사는 실제 API 키 없이 수행합니다. 로고의 가짜 네트워크 응답, 캐시, 실패 시 대체 표시, 이미지 요청의 인증 헤더 제외를 테스트했습니다. `0.1.1` wheel을 네트워크와 API 키 없이 빌드하여 명시한 PNG 6개와 출처 문서가 원본과 같은 내용으로 포함되고 민감 파일 경로가 포함되지 않는 것을 확인했습니다.

실제 로고 CDN 접속과 다운로드는 현재 개발 환경의 네트워크 정책으로 검증하지 못했습니다. 2026-10-21의 라이브 검증에서는 API에 2026-10-21~27 날짜 범위로 총 3회 요청하여 PL 10경기·PD 11경기·CL 9경기, 총 30경기·53개 팀을 받아 처리했습니다. 한글 표기가 누락된 팀명 8개를 보완하고 회귀 테스트를 추가했습니다. 키와 응답 원문은 파일이나 로그에 남기지 않았고, 모델에 스코어가 저장되지 않는 것도 확인했습니다. 일정 API 연결 확인과 로고 CDN 연결 확인은 별개입니다.

Windows `.exe` 빌드와 실행은 해당 로고 버전 커밋의 Windows Actions 결과를 확인해야 합니다. Windows 자격 증명 관리자 실사용, 사용자 PC의 수동 실행 및 고해상도 화면 배치는 해당 환경에서 별도 확인이 필요합니다.
