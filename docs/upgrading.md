# バージョンアップ

対象はThrowTaskServerのAPI・CLI・stdio MCPです。iOS/macOS/Watchアプリは別配布で、先にサーバーを対応版へ更新します。`v0.2.0`は改名前のToiDone版で、改名後のコードを含みません。ThrowTask版はCI成功済みの改名後commit SHAを指定します（改名後のreleaseタグは未作成）。タグが指すcommitの[Tests](https://github.com/irohamo/ThrowTaskServer/actions/workflows/test.yml)成功を確認してから進めます。

## Debianの常駐サーバー

`/opt/throwtask-server`のGit checkoutと`.venv`、`/var/lib/throwtask`のDB、[systemd unit](../deploy/throwtask-server.service)を使う配置に対応します。Python 3.12以上、git、uv、systemd、runuserが必要です。別の配置は`--repo`/`--data-dir`/`--service`/`--port`で指定できます。設定と異なるサービスや、変更の残ったcheckoutには適用しません。

最初に公開済みのcommitを取得し、その版の更新プログラムだけを一時ディレクトリへ取り出します。**稼働中のcheckoutを先に切り替えないでください。** 現行のeditableインストールはソース変更の影響を受けます。

```bash
upgrade_ref='<改名後のCI成功commitの完全SHA>'
upgrade_uv="$(command -v uv)"
upgrade_directory="$(mktemp -d)"
sudo git -C /opt/throwtask-server fetch --tags origin
sudo git -C /opt/throwtask-server show "$upgrade_ref:deploy/upgrade.py" > "$upgrade_directory/upgrade.py"
```

uvが見つからない場合は[セットアップ](../README.md)に従ってインストールしてから進めます。認証情報を含むURLやAPIキーを引数へ渡す必要はありません。

まず計画を表示します。この呼び出しはサービス・DB・依存環境・ソースを変更せず、Gitのfetchもしません。

```bash
sudo /usr/bin/python3 "$upgrade_directory/upgrade.py" \
  --ref "$upgrade_ref" --uv "$upgrade_uv"
```

`ok=true`、`previousRevision`、`targetRevision`、`targetVersion`、対象のパスとサービスを確認して実行します。`--ref`はタグの代わりに完全なcommit SHAも指定できます。

```bash
sudo /usr/bin/python3 "$upgrade_directory/upgrade.py" \
  --ref "$upgrade_ref" --uv "$upgrade_uv" --apply
```

実行内容は次の順です。

1. 指定commitのソースを一時展開し、固定された依存関係でlint・format check・全テストを実行する。ここでは常駐APIを停止しない。
2. 稼働中DBをSQLite backup APIで保存し、一時復元先で更新先の`restore`/`init`を試す。
3. 設置状態が変わっていないことを再確認してAPIを停止し、停止時点のDBをもう一度バックアップする。
4. ソースを指定commitへ切り替え、`.venv`をruntime依存だけに同期する。この同期は事前検証で用意したcacheを使うoffline実行。
5. サービス実行ユーザーで既存DBを初期化し、APIを起動する。localhostのhealth・実行中のversion・systemdのactive状態を確認する。

依存関係は[`uv sync --frozen`の公式仕様](https://docs.astral.sh/uv/concepts/projects/sync/)に従い`uv.lock`へ固定します。更新先のテストも一時DB・専用キーを使い、常駐APIの認証情報を使いません。

成功すると`phase=complete`を返します。DBのタスク・revision・差分履歴・キーのハッシュと失効状態を継続して使い、CLIの設定やKeychain/keyringは変更しません。APIキーの再発行や`configure`のやり直しは不要です。`.venv/bin`への既存のCLI/MCPリンクも同じパスを使えます。

### 旧ToiDone名の配置を更新する場合

既存の`/opt/toidone-server`・`/var/lib/toidone`・`toidone-server.service`はそのまま使います。
上の取得コマンドのGitパスを既存のcheckoutへ置き換え、計画・実行の両方に次を指定します。

```bash
sudo /usr/bin/python3 "$upgrade_directory/upgrade.py" \
  --repo /opt/toidone-server --data-dir /var/lib/toidone \
  --service toidone-server.service --ref "$upgrade_ref" --uv "$upgrade_uv"
```

計画確認後、同じ指定に`--apply`を加えます。新規パスがなく旧標準配置が存在する場合は自動選択もできますが、独自配置では全項目を明示します。DBファイル・サービスアカウント・既存CLI/MCP起動パスを変更する必要はありません。

## 更新後の確認

```bash
/opt/throwtask-server/.venv/bin/throwtask --version
/opt/throwtask-server/.venv/bin/throwtask-server --version
/opt/throwtask-server/.venv/bin/throwtask-mcp --version
throwtask --json auth status
throwtask tasks list --limit 1 --json
```

`--version`は0.2.0以降で利用できます。APIの`GET /healthz`も`data.version`を返します。envelopeの`version=1`はAPI契約の版で、製品版番号とは別です。

稼働中のstdio MCPプロセスには古いコードが残るため、MCPクライアントで接続をいったん切って再接続します。`auth_status`と必要なtoolsのschemaを確認します。認証エラーは再試行せず、設定したキーと失効状態を確認します。

今回のbot向け拡張は[利用例](bots.md)にあります。通常の動作確認は読み取りで行い、書き込みを試す場合は専用UUIDに限定します。

## 失敗した場合

実行ごとにbackup-dir（既定`/var/lib/throwtask/backups`）へ次のファイルを残します。バックアップと記録・ログを自動削除しません。

| ファイル | 内容 |
| --- | --- |
| `upgrade-<id>.json` | 前後のcommit、対象version、処理段階、バックアップとログのパス |
| `upgrade-<id>.log` | 依存同期・テスト・管理コマンドの結果。所有者のみ閲覧可能 |
| `upgrade-<id>-preflight.sqlite3` | 事前検証用の稼働中バックアップ |
| `upgrade-<id>-stopped.sqlite3` | 停止後・切り替え前のバックアップ |

`DIRTY_WORKTREE`なら利用者の変更を保持して終了します。stash・reset・force checkoutを自動実行しません。`UPGRADE_IN_PROGRESS`なら別の更新が実行中です。同じcommitへの適用は何も変更しません。

自動更新先はhealthで版を返せる0.2.0以降です。0.1系への切り替えは`TARGET_VERSION_UNSUPPORTED`で停止前に拒否し、以下の手動復旧を使います。現在の起動コマンドを提供しない版は`TARGET_COMMAND_UNSUPPORTED`、ThrowTask名のDBを開けない旧配布名の版は`TARGET_LAYOUT_UNSUPPORTED`で拒否します。

事前検証中の失敗は稼働中のサービスを止めません。停止処理に入った後の失敗ではサービスを停止し、DBとバックアップを保持します。JSONの`serviceStopped=false`なら停止確認に失敗しているので、先に`systemctl status`を確認してください。DBの自動復元や、自動での旧版起動は行いません。起動後の確認中に新しい書き込みが入っている可能性もあるためです。

### 旧版へ戻す

1. APIを停止し、`failedPhase`・`previousRevision`・`stoppedBackup`を記録する。更新後に書き込みがあった場合は、現在のDBもSQLite backup APIで別途保存する。
2. `git status --porcelain`が空であることを確認し、`previousRevision`へcheckoutする。`uv sync --frozen --no-dev --python /usr/bin/python3 --no-python-downloads`で旧依存を復元する。
3. 旧版が現在の保存形式に対応していることを、その版の変更履歴とDBのコピーで確認する。対応が保証される場合だけ同じdata-dirで起動する。
4. 対応しない場合は、停止時バックアップを**新しいdata-dir**へ復元する。元のDBは保持する。systemdの`ExecStart`と`ReadWritePaths`、バックアップjobのdata-dirを復元先に合わせ、`daemon-reload`後に起動する。
5. 認証状態、読み取り、CLI/MCPの接続を確認してから書き込みを再開する。バックアップ後の失効は復元されないため、該当キーを起動前に再失効させる。更新後のデータは保持したDBから別途照合する。

0.2.0ではJSONに新フィールドを追加します。**新フィールドを保存した後の0.1.0へのコードだけの差し戻しはサポートしません。** SQLiteの`user_version`が同じ1でも、旧版の入力検証が新フィールドを拒否します。停止時バックアップからの新規復元を使います。

復元コマンドの例です。パスは記録したものに置き換え、空の復元先をサービスユーザー所有で用意します。既存DBへ上書きしません。

```bash
sudo install -d -o throwtask -g throwtask -m 700 /var/lib/throwtask-rollback
sudo -u throwtask /opt/throwtask-server/.venv/bin/throwtask-admin \
  --data-dir /var/lib/throwtask-rollback restore --backup /path/to/upgrade-id-stopped.sqlite3
```

systemdのoverride例です。既存のoverrideがある場合は内容を保持して編集します。

```ini
[Service]
ExecStart=
ExecStart=/opt/throwtask-server/.venv/bin/throwtask-server --data-dir /var/lib/throwtask-rollback --host 127.0.0.1 --port 8766
ReadWritePaths=/var/lib/throwtask-rollback
```

## 別端末に入れたCLI/MCP

`upgrade_ref`にはサーバーと同じ完全なcommit SHAを設定します。

サーバーの後に、各端末のcheckoutを同じ検証済みcommitへ合わせます。未コミットの変更があれば先に保存し、設定・資格情報ファイルは残します。

```bash
git status --short
git fetch --tags origin
git checkout --detach "$upgrade_ref"
uv sync --frozen --no-dev
.venv/bin/throwtask --version
.venv/bin/throwtask --json auth status
```

uv tool/pipなど別の方法で導入した場合は、元の導入方法で同じ版へ更新します。MCPは更新後に再接続します。

## 公開する側

公開前に`pyproject.toml`と`src/throwtask/__init__.py`のversion、`uv.lock`、[変更履歴](../CHANGELOG.md)を揃えます。`uv lock`は既存lockを維持し、依存更新が目的でなければ`--upgrade`を付けません。

```bash
uv sync --frozen
uv run ruff check src tests deploy
uv run ruff format --check src tests deploy
uv run pytest -q
```

公開対象の変更をレビュー・commitし、検証したcommitへ`v<version>`のタグを付けます。mainとタグをpushしてCIを確認してから、利用者がそのタグを更新先に指定します。アプリのソースや個人用の配置記録はサーバーリポジトリへ含めません。
