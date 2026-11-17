# ユーザーとパスキー

アプリはユーザーIDと標準パスキーでログインし、CLI・MCP・botはユーザーに紐づく個別APIキーを使います。パスキーとアプリセッションはMCPへ渡しません。認証設定がない場合は従来のAPIキー接続を継続できます。

## 接続先と認証ドメイン

| 設定 | 例 | 用途 |
| --- | --- | --- |
| `serverURL` | `https://192.0.2.10:8443` | 招待に含めるAPIの接続先。IPとポートも可能 |
| `rpID` | `auth.example.com` | パスキーを保存するドメイン。IP・スキーム・ポートを含めない |
| `origins` | `["https://auth.example.com"]` | 署名検証で許可するclientDataのorigin |
| `appleAppIDs` | `["ABCDE12345.com.example.ThrowTask"]` | AASAの`webcredentials.apps`。実際のTeam IDとBundle IDを設定 |

APIのTLS証明書は接続先のホストに一致させ、IPでも証明書検証を省略しません。HTTPはloopbackだけです。`localhost`はサーバー開発テスト用で、iOSの関連付けを省略するものではありません。

iOSは`webcredentials`のAssociated Domainがないとパスキー登録・認証でエラーになります。RPホストの`https://<rpID>/.well-known/apple-app-site-association`をリダイレクトなしで配信します。本APIは同じパスで`appleAppIDs`からAASAを生成するため、RPホスト側でこのパスをreverse proxyするか同じJSONを配信します。TLSやAppleの関連付けはサーバープログラムの外で設定します。[Apple公式](https://developer.apple.com/documentation/authenticationservices/supporting-passkeys)。

Apple CDNから到達できないプライベートホストでは、開発署名・端末のAssociated Domains Development設定と`?mode=developer`などの条件を確認します。通常配布で同じ挙動になるとは扱いません。[Associated Domainsの要件](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.associated-domains)。

アプリ側も対応RP・Associated Domains・API URLを設定します。招待やサーバーが返すRPだけを信頼して任意の第三者APIへセッションを送らないでください。ドメイン未定の間は`auth.json`を作成せず、APIキーを利用できます。

## 設定と初回登録

サービスのdata-dirに`auth.json`を保存し、再起動で読み込みます。以下は例で、ホスト・App IDを管理対象に置き換えます。秘密は含みません。設定なしでは`GET /v1/auth/config`の`passkeysEnabled=false`、不正な設定では起動を拒否します。

```json
{
  "rpID": "auth.example.com",
  "serverURL": "https://192.0.2.10:8443",
  "origins": ["https://auth.example.com"],
  "appleAppIDs": ["ABCDE12345.com.example.ThrowTask"]
}
```

スキーマ1のDBは起動・`init`時に2へ移行し、既存のタスク・プロジェクト・APIキーを1人のlegacy所有者へ束ねます。UUID・revision・作成fingerprint・繰り返しリンク・差分カーソル・失効状態を保ちます。更新前に[更新手順](upgrading.md)で停止時バックアップを確保します。

管理者が本人に渡す招待だけを`--claim-legacy`で発行します。最初の任意の登録者へ既存データを渡しません。

```bash
umask 077
mkdir -p .secrets
throwtask-admin --data-dir /path/to/data invite \
  --claim-legacy --output .secrets/owner-invitation.json \
  --qr-output .secrets/owner-invitation.png
```

JSON/PNGは新規0600ファイルへ保存し、標準出力は期限・UUID・ファイルパスだけです。ファイル・画像を安全に本人へ渡し、秘密を会話・ログ・Gitへ貼らないでください。招待は既定30分、署名検証後に1回だけ消費します。`--lifetime`は60〜86400秒です。legacy招待の再発行は以前の未使用招待を無効にします。

別ユーザーには`--claim-legacy`なしで発行します。空のデータから始まり、他ユーザーのタスクを読めません。

```bash
throwtask-admin --data-dir /path/to/data invite \
  --output .secrets/new-user.json --qr-output .secrets/new-user.png
throwtask-admin --data-dir /path/to/data users
```

アプリで招待QR・画像・ファイルを読み、接続先を確認し、ユーザーIDとパスキーを登録します。IDは半角英数字・ピリオド・ハイフン・アンダースコアの3〜32文字で小文字に統一します。サーバーはchallenge・origin・RP ID・UVを検証し、ログインでは署名・カウンター・userHandleも確認します。challengeは5分・1回限りで、検証失敗後の再利用も拒否します。

## セッションと復旧

アクセスは15分、更新は30日です。更新トークンは1回ごとにローテーションし、使用済みの再利用で同じセッションを失効させます。通信断などで更新結果が不明なら再送せず、パスキーでログインし直します。ログアウトで当該セッションを失効させます。サーバーはハッシュだけを保存します。

登録・復旧時に8個の復旧コードを返します。クライアントは安全な保存を促し、秘密ストア以外へ永続化しません。各1回限りで、サーバーはハッシュのみ保存します。復旧用options取得時にコードを消費します。新パスキーの署名検証後に以前のパスキーと全アプリセッションを失効させ、コードを再発行します。botキーは別管理なので残ります。

コード再発行には5分以内のパスキーログインが必要で、APIキーや単なる更新では代用できません。復旧・再発行後は、古いコードで取得した未完了の復旧challengeも使えません。全パスキーと全復旧コードを失った場合の本人確認を自動で管理者へ委任する機能はありません。

## botキーとユーザー分離

`users`のUUIDを指定して発行します。`--user`なしは既存互換のためlegacy所有者です。

```bash
throwtask-admin --data-dir /path/to/data token create briefing-bot \
  --user <user-uuid> --read-only --output .secrets/briefing.credential
throwtask-admin --data-dir /path/to/data token create task-bot \
  --user <user-uuid> --output .secrets/task-bot.credential
```

読み取り専用キーでの変更は403です。CLI/MCPの`configure`と鍵保管方法は従来どおりで同じAPI clientを使います。所有者は資格情報から決め、tool引数やbodyでは指定しません。タスクUUID・外部ID・プロジェクト参照・削除・繰り返し・snapshot・差分カーソルはユーザー単位で分離します。

## 認証API

公開フローは証明と送信元の回数制限を必要とします。bodyをアクセスログへ記録せず、秘密をqueryへ含めません。

| エンドポイント | 入力 → 結果 |
| --- | --- |
| `GET /v1/auth/config` | 秘密のない有効状態・RP ID |
| `POST /v1/auth/register/options` | invitation・username → challengeID・options |
| `POST /v1/auth/register/verify` | challengeID・credential → user・session・recoveryCodes |
| `POST /v1/auth/login/options` | 発見可能パスキー用options |
| `POST /v1/auth/login/verify` | challengeID・credential → user・session |
| `POST /v1/auth/session/refresh` | refreshToken → ローテーション済みsession |
| `POST /v1/auth/session/logout` | refreshToken → 失効 |
| `POST /v1/auth/recovery/options` | username・recoveryCode → 新パスキー登録options |
| `POST /v1/auth/recovery-codes` | 最近認証したBearer session → 新コード |
| `GET /v1/auth/status` | Bearer APIキー/session → user・method・canWrite・expiresAt |

認証フローをMCP toolにはしません。401や結果不明時は自動再試行せず、利用者の認証操作に戻します。生成OpenAPIも参照してください。
