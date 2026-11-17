# Debianへの配置

Python 3.12以上で動きます。開発時の手順は[README](../README.md)を参照してください。

## サービス化

ソースと仮想環境を`/opt/throwtask-server`に配置し、専用の`throwtask`ユーザーが読み取り・実行できるようにします。データは`/var/lib/throwtask`に置きます。管理者のホーム配下のPythonに依存させず、Debian 13のシステムPythonも使えます。

```bash
uv sync --frozen --no-dev --python /usr/bin/python3
```

この場合は以降のコマンドを`.venv/bin/throwtask-admin`や`.venv/bin/throwtask-server`で実行します。[systemd unitの例](../deploy/throwtask-server.service)を配置し、利用者の環境に合わせてユーザーとパスを設定します。この例のインストール・常駐運用はまだ検証していません。

APIはloopbackへbindし、外から使うときはHTTPSのリバースプロキシを前段に置きます。CLIはloopback以外のHTTPを既定で拒否します。信頼できる開発ネットワークでHTTPを使うときだけ、configureの`--allow-insecure-http`を明示します。iOSアプリの通常接続にはHTTPSを使います。

## 端末ごとのキー

管理コマンドはサーバーと同じOSユーザーで実行します。端末ごとに別のキーを作り、所有者限定の新規ファイルに出力します。既存ファイルは上書きしません。端末へ移すときはSSH/SCPなどを使い、コピー先も0600にします。キーをチャットやログへ貼りません。

```bash
throwtask-admin --data-dir /var/lib/throwtask token create iphone --output /secure/path/iphone.credential
throwtask-admin --data-dir /var/lib/throwtask token list
throwtask-admin --data-dir /var/lib/throwtask token revoke <token-uuid>
```

APIキーに有効期限はなく、失効させるまで有効です。各API要求でDBの失効状態を確認します。CLIはOS keyring、または明示した所有者限定・通常ファイルの資格情報を読みます。暗黙の平文保存へのfallbackは行いません。

iOS/macOSアプリでは「同期の設定」でHTTPS URLとその端末用キーを設定します。キーは端末のKeychainへ保存します。初回接続では端末内データを既存UUIDで追加し、同じUUIDの内容が食い違う場合は競合として保留します。元のローカルデータも保持します。

## バックアップと復元

SQLiteのbackup APIでWALを含む整合したバックアップを作ります。保存先は新規ファイルに限り、0600で作成します。バックアップにはタスク、差分履歴、APIキーのハッシュと失効状態が含まれます。

```bash
throwtask-admin --data-dir /var/lib/throwtask backup --output /secure/backups/throwtask-2026-11-15.sqlite3
throwtask-admin --data-dir /var/lib/throwtask-restored restore --backup /secure/backups/throwtask-2026-11-15.sqlite3
```

復元は新しいdata-dirで行い、既存DBを上書きしません。検証後にサーバーのdata-dirを切り替えます。バックアップ時点の認証状態も復元されるため、利用するキーと失効状態を見直してください。バックアップ先・世代管理・暗号化は運用側で設定します。

## 検証の範囲

既存の配置を更新する場合は[バージョンアップ方法](upgrading.md)を使います。Gitの版を指定し、事前検証・DBコピーでの初期化確認・停止時バックアップ・依存同期・再起動確認を行います。CLI設定と資格情報の保存先は保持します。

一時環境でのDebian CRUDとMCP接続の成功は、常駐サービス、公開HTTPS、iPhone実機の検証を意味しません。[検証記録](verification.md)に実際に確認した範囲を記載します。
