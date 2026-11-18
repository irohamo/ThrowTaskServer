# APIキーによる認証とユーザー管理

0.5.0から、アプリ・CLI・MCP・botはAPIキーだけで認証します。パスキー、招待、復旧コード、アプリセッション認証は提供しません。

## 初回のキー発行

サーバー管理者がdata-dirを指定して発行します。最初のキーは既存データの所有者に紐づきます。端末やbotごとに別のキーを作り、値は新しい0600ファイルへだけ出力します。

```bash
uv run throwtask-admin --data-dir ./data init
uv run throwtask-admin --data-dir ./data token create iphone --output ./iphone.credential
uv run throwtask-admin --data-dir ./data token create briefing-bot --read-only --output ./briefing.credential
```

アプリはサーバーURLとその端末専用キーを設定し、Keychainへ保存します。CLI/MCPは同じAPIクライアントを使い、OS keyringまたは明示的な本人所有0600の資格情報ファイルから読みます。キーをコマンド引数、環境変数、設定JSON、Git、ログ、会話、MCP toolの引数・結果へ出さないでください。

APIキーは期限なし・手動失効です。サーバーにはSHA-256ハッシュだけを保存します。旧版で発行済みのキーを維持するため、`mtd_`の形式もそのまま使用します。HTTPSの証明書は通常どおり検証します。

## 利用者を分ける

管理者が利用者を作成し、その不変UUIDを指定してキーを発行します。ユーザーIDは半角英数字・ピリオド・ハイフン・アンダースコアの3〜32文字で、小文字に正規化します。

```bash
uv run throwtask-admin --data-dir ./data user create alice
uv run throwtask-admin --data-dir ./data users
# 次のuser引数は、作成時に表示されたUUIDへ置き換えます。
uv run throwtask-admin --data-dir ./data token create alice-iphone --user '作成時に表示されたUUID' --output ./alice-iphone.credential
```

タスク・プロジェクト・UUID・外部IDの一意性・差分カーソルはユーザー単位です。他の利用者のデータは取得・参照・更新できません。読み取り専用キーはGETだけを許可します。管理操作の利用者指定を、一般のタスクAPIやMCPから上書きすることはできません。

## 失効と再接続

```bash
uv run throwtask-admin --data-dir ./data token list
uv run throwtask-admin --data-dir ./data token revoke '失効対象のキーUUID'
```

一覧に秘密の値は含めません。紛失した端末・停止したbotのキーだけを失効し、新しいキーを発行します。認証失敗後に書き込みを自動で再送しません。新しい資格情報で接続を保存し直してください。変更・削除には引き続きrevisionを使います。

## 0.3.0 / 0.4.0からの移行

既存のレコード、キー、ユーザーUUID、差分カーソルとDB schema 2を維持します。旧パスキー関連のテーブルはバックアップ互換性のため保持しますが、認証や更新に使用しません。有効期限内の旧セッションも拒否します。

`GET /v1/auth/config`は`authenticationMethods=["apiKey"]`を返します。旧クライアント向けに`passkeysEnabled=false`、`rpID=null`も返します。登録・ログイン・セッション更新・復旧・AASAのendpointは削除しました。旧`auth.json`は読みません。

パスキー用Associated Domains・AASA公開・Funnelは不要です。既存のreverse proxyからAPIへ接続し、不要になったgatewayや公開経路を整理してください。Tailscale内に配置する場合はクライアントも同じTailscaleへ接続します。稼働構成の変更前に設定とDBをバックアップし、APIを意図せず外部公開しないようにしてください。
