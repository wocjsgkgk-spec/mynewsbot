# GitHub Actions Telegram News Bot

Google Apps Script의 UrlFetch 하루 한도를 피하기 위한 GitHub Actions 버전입니다.

## 동작 구조

```text
GitHub Actions
→ 5분마다 Python 실행
→ Google News RSS 확인
→ 최근 30분 기사만 선택
→ Google News 링크를 원문 기사 링크로 변환
→ Telegram 채널 전송
→ sent_articles.json에 전송 기록 저장
→ 변경된 기록 파일 자동 커밋
```

## 파일 구성

```text
.github/workflows/telegram-news-bot.yml
github-actions-news-bot/news_bot.py
github-actions-news-bot/sent_articles.json
```

## GitHub Secrets 설정

GitHub 저장소에서 아래로 이동합니다.

```text
Settings → Secrets and variables → Actions → New repository secret
```

아래 2개를 추가합니다.

```text
BOT_TOKEN = 텔레그램 봇 토큰
CHAT_ID = @텔레그램채널아이디
```

예시:

```text
CHAT_ID = @wocjsgkgknews
```

텔레그램 봇은 채널 관리자로 추가되어 있어야 하며, 메시지 게시 권한이 필요합니다.

## GitHub Actions 권한 설정

저장소에서 아래로 이동합니다.

```text
Settings → Actions → General
```

아래 설정을 확인합니다.

```text
Workflow permissions
→ Read and write permissions 선택
→ Save
```

이 권한이 있어야 `sent_articles.json` 전송 기록을 자동 커밋할 수 있습니다.

## 수동 실행

저장소에서 아래로 이동합니다.

```text
Actions → Telegram News Bot → Run workflow
```

처음에는 수동 실행으로 테스트하세요.

## 자동 실행

워크플로는 기본적으로 5분마다 실행됩니다.

```yaml
cron: "*/5 * * * *"
```

GitHub Actions 스케줄은 무료로 쓸 수 있지만, 정확히 매 5분 정각에 실행된다는 보장은 없습니다.
몇 분 늦게 실행될 수 있습니다.

## 설정 변경

`.github/workflows/telegram-news-bot.yml`에서 숫자만 바꾸면 됩니다.

```yaml
REALTIME_ONLY_MINUTES: "30"
MAX_RSS_ITEMS_TO_CHECK: "5"
MAX_MESSAGES_PER_RUN: "4"
```

- `REALTIME_ONLY_MINUTES`: 최근 몇 분 이내 기사만 보낼지
- `MAX_RSS_ITEMS_TO_CHECK`: 카테고리별 확인할 기사 수
- `MAX_MESSAGES_PER_RUN`: 한 번 실행할 때 최대 전송 수

## 무료 사용 관련

Public repository는 GitHub-hosted runner 사용이 무료입니다.
Private repository도 무료 분량이 있지만, 계정 플랜과 사용량에 따라 제한이 있습니다.

비용 걱정을 줄이려면 public 저장소를 쓰되, 토큰은 반드시 GitHub Secrets에만 넣으세요.
코드에 토큰을 직접 적으면 안 됩니다.
