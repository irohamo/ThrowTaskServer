# ThrowTask Server

[GitHub](https://github.com/irohamo/ThrowTaskServer) · [CI](https://github.com/irohamo/ThrowTaskServer/actions/workflows/test.yml)

**ThrowTask（スロータスク）** — タスクを、気軽に投げ込む。

個人利用向けのセルフホストToDo API、Debian CLI、MCPサーバーです。Python 3.12以上、FastAPI、SQLite、公式MCP Python SDKを使います。iOS/macOSアプリのソースは別管理です。

- タスク・プロジェクトのCRUD、完了/再開、検索、ページング。
- ユーザー別データ・招待登録・パスキーログイン・復旧コード。アプリのセッションとbotの個別APIキーを分離。[認証手順](docs/authentication.md)。
- 期限なし・手動失効可能なユーザー別APIキー、読み取り専用キー。サーバーにはハッシュを保存。
- revisionによる条件付き更新。削除プレビュー、論理削除、復元。
- チェックリスト、タグ、予定日と締切、Someday、繰り返し。
- bot用の外部ID重複防止・作成元、待ち/ブロック状態、時刻からの差分取得。[利用例](docs/bots.md)。
- 共通クライアントを使うCLIとstdio MCP（17 tools）。
- 同期用snapshot/差分API、SQLiteバックアップ/新規保存先への復元。
- 指定した版への更新計画・事前検証・停止時バックアップ。[バージョンアップ方法](docs/upgrading.md)。

## セットアップ

[uv](https://docs.astral.sh/uv/getting-started/installation/)とPython 3.12以上を用意し、このディレクトリで実行します。改名前の版はDebian 13のPython 3.13でも検証済みです。改名後の版の検証範囲は[記録](docs/verification.md)を参照してください。

```bash
git clone https://github.com/irohamo/ThrowTaskServer.git
cd ThrowTaskServer
uv sync --frozen
uv run throwtask-admin --data-dir ./data init
mkdir -m 700 .secrets
uv run throwtask-admin --data-dir ./data token create debian --output .secrets/debian.credential
uv run throwtask-server --data-dir ./data
```

サーバーは標準で `127.0.0.1:8766` を待ち受けます。キーは指定した新規ファイルに0600で保存し、画面に出しません。キーと個人データをGitへ追加しないでください。

別のターミナルでCLIを設定します。設定ファイルには接続先と資格情報ファイルのパスを保存します。

```bash
uv run throwtask configure --url http://127.0.0.1:8766 --credential-file "$PWD/.secrets/debian.credential"
uv run throwtask --json auth status
uv run throwtask tasks create "最初のタスク" --when 2026-11-15 --json
uv run throwtask tasks list --json
```

`--credential-file`を省くと、キーを隠し入力してmacOS Keychain / Linux Secret Serviceに保存します。headlessでは、明示的に指定した所有者限定の資格情報ファイルも利用できます。キーそのものをコマンド引数や環境変数で渡す機能はありません。

編集には取得した`revision`を指定します。次のIDは作成結果のUUIDに置き換えます。

```bash
uv run throwtask tasks get <uuid> --json
uv run throwtask tasks edit <uuid> --revision 1 --notes "メモ" --json
uv run throwtask tasks complete <uuid> --revision 2 --json
uv run throwtask tasks reopen <uuid> --revision 3 --json
uv run throwtask tasks delete <uuid> --revision 4 --dry-run --json
uv run throwtask tasks delete <uuid> --revision 4 --confirm --json
uv run throwtask tasks restore <uuid> --revision 5 --json
```

CLIはJSON envelope v1を返します。exit 0は成功、1はAPI/認証/競合、2はローカル入力/設定エラーです。作成の結果が不明な場合はエラーの`recordId`を取得して確認します。書き込みと認証失敗を自動再試行しません。

## MCP

同じ設定ファイルを使って起動します。

```bash
uv run throwtask-mcp
```

MCPクライアントの設定例です。`/absolute/path/ThrowTaskServer`を実際の場所に置き換えます。キーは設定に含めません。

```json
{
  "mcpServers": {
    "throwtask": {
      "command": "/absolute/path/ThrowTaskServer/.venv/bin/throwtask-mcp",
      "args": []
    }
  }
}
```

独自のCLI設定を使う場合は`args`に`["--config", "/absolute/path/config.json"]`を指定します。MCPの標準出力にはプロトコル以外を出しません。

`list_tasks`、`get_task`、`create_task`、`update_task`、`complete_task`、`reopen_task`、`delete_task`、`restore_task`と、プロジェクト操作、認証確認、snapshot/差分取得を提供します。更新には`expected_revision`が必須です。削除はプレビューが既定で、実行には`confirm=true`と`dry_run=false`の両方が必要です。

## 検証

```bash
uv run ruff check src tests deploy
uv run ruff format --check src tests deploy
uv run pytest -q
```

テストは一時DBと専用UUIDを使います。結合テストは実際のHTTPサーバー、CLIプロセス、公式MCPクライアントとstdioサーバーを起動します。[検証記録](docs/verification.md)に実際の結果を記載します。iPhone実機の双方向反映をこれらのテストで証明したとは扱いません。

[API契約](docs/api.md) · [Debianへの配置とバックアップ](docs/self-hosting.md) · [更新](docs/upgrading.md) · [MVPの進捗](docs/mvp.md)

## ToiDoneからの改名

2026-11-17に製品とリポジトリをThrowTaskへ変更しました。`v0.2.0`タグは改名前のToiDone版を指したままです。改名とユーザー認証を含む版は`v0.3.0`です。[更新手順](docs/upgrading.md)に従い、対象タグのCI成功を確認して指定します。

パッケージは`throwtask-server`、Python moduleは`throwtask`、コマンドは`throwtask`、
`throwtask-server`、`throwtask-admin`、`throwtask-mcp`です。旧`toidone*`と`mytodo*`の4コマンドずつも互換aliasとして使えます。旧`python -m toidone.cli`・`server`・`admin`・`mcp_server`と、共通client/config/storage等のimportも新実装へ委譲します。

新規環境のCLI設定は`~/.config/throwtask/config.json`、サーバーデータは
`~/.local/share/throwtask-server/throwtask.sqlite3`です。新しい既定パスがなければ、既存の`toidone`、`mytodo`の順で設定・保存先を探します。指定data-dir内のDBは`throwtask.sqlite3`、`toidone.sqlite3`、`mytodo.sqlite3`の順で既存ファイルを開きます。
既存のキー・失効状態・タスク・revisionを保持し、DBやWALを自動で移動しません。新旧両方がある場合は新名を優先し、壊れた新設定やアクセスできないキーを旧コピーで隠しません。

OS keyringも`throwtask:`サービスを優先し、未登録の場合だけ`toidone:`、`mytodo:`を読み取ります。既存キーの形式とAPI v1は継続します。`--config`と`--data-dir`の明示指定も使えます。

既存systemd配置のアカウント・サービス名・パスは変更不要です。更新プログラムは従来の`toidone-server` / `mytodo-server`の起動パスと管理コマンドにも対応します。新規配置は[ThrowTask名のunit](deploy/throwtask-server.service)を使います。
