# MatterMost-Notice-Bot
GoogleカレンダーのiCal公開URLを定期取得し、直近の予定をMattermostに通知する仕組みの検討・実装を行うリポジトリです。
- 要件定義: [docs/requirements.md](docs/requirements.md)

## セットアップ
GitHub Actions を利用して定期通知を行います。以下のSecrets/Variablesを設定してください。

### 必須
- `ICAL_URL`: 通知対象のGoogleカレンダー iCal 公開URL
- `MATTERMOST_WEBHOOK_URL`: Mattermost Incoming Webhook URL
- `NOTICE_WINDOW_MINUTES`: 何分以内に開始する予定を通知するか（整数）

### 任意
- `TIMEZONE`: 表示用タイムゾーン（例: `Asia/Tokyo`。未指定時はUTC）
- `MAX_EVENTS`: 1回の通知で扱う最大件数

### ワークフロー
`.github/workflows/notify.yml` で 15 分おきと手動トリガーを定義しています。状態ファイルは `state/notifications.json` に保存し、`actions/cache` を使って次回以降に引き継ぎます。

## ローカルテスト
Python 3.11 以降を想定しています。
```
pip install -r requirements.txt
export ICAL_URL="..."
export MATTERMOST_WEBHOOK_URL="..."
export NOTICE_WINDOW_MINUTES=60
python -m src.notifier
```
