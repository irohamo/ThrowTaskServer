# ToiDone Server

**ToiDone（トイダン）**は、当為（なすべきこと）＋Done（完了）。なすべきことを、完了に。

個人利用向けのセルフホストToDo API、Debian CLI、MCPサーバーです。Python 3.12以上、FastAPI、SQLite、公式MCP Python SDKを使います。iOS/macOSアプリのソースは別管理です。

- タスク・プロジェクトのCRUD、完了/再開、検索、ページング。
- 期限なし・手動失効可能な端末別APIキー。サーバーにはキーのハッシュを保存。
- revisionによる条件付き更新。削除プレビュー、論理削除、復元。
- チェックリスト、タグ、予定日と締切、Someday、繰り返し。
- 共通クライアントを使うCLIとstdio MCP（17 tools）。
- 同期用snapshot/差分API、SQLiteバックアップ/新規保存先への復元。

## セットアップ

[uv](https://docs.astral.sh/uv/getting-started/installation/)とPython 3.12以上を用意し、このディレクトリで実行します。Debian 13のPython 3.13でも検証済みです。

```bash
uv sync --frozen
uv run toidone-admin --data-dir ./data init
mkdir -m 700 .secrets
uv run toidone-admin --data-dir ./data token create debian --output .secrets/debian.credential
uv run toidone-server --data-dir ./data
```

サーバーは標準で `127.0.0.1:8766` を待ち受けます。キーは指定した新規ファイルに0600で保存し、画面に出しません。キーと個人データをGitへ追加しないでください。

別のターミナルでCLIを設定します。設定ファイルには接続先と資格情報ファイルのパスを保存します。

```bash
uv run toidone configure --url http://127.0.0.1:8766 --credential-file "$PWD/.secrets/debian.credential"
uv run toidone --json auth status
uv run toidone tasks create "最初のタスク" --when 2026-11-15 --json
uv run toidone tasks list --json
```

`--credential-file`を省くと、キーを隠し入力してmacOS Keychain / Linux Secret Serviceに保存します。headlessでは、明示的に指定した所有者限定の資格情報ファイルも利用できます。キーそのものをコマンド引数や環境変数で渡す機能はありません。

編集には取得した`revision`を指定します。次のIDは作成結果のUUIDに置き換えます。

```bash
uv run toidone tasks get <uuid> --json
uv run toidone tasks edit <uuid> --revision 1 --notes "メモ" --json
uv run toidone tasks complete <uuid> --revision 2 --json
uv run toidone tasks reopen <uuid> --revision 3 --json
uv run toidone tasks delete <uuid> --revision 4 --dry-run --json
uv run toidone tasks delete <uuid> --revision 4 --confirm --json
uv run toidone tasks restore <uuid> --revision 5 --json
```

CLIはJSON envelope v1を返します。exit 0は成功、1はAPI/認証/競合、2はローカル入力/設定エラーです。作成の結果が不明な場合はエラーの`recordId`を取得して確認します。書き込みと認証失敗を自動再試行しません。

## MCP

同じ設定ファイルを使って起動します。

```bash
uv run toidone-mcp
```

MCPクライアントの設定例です。`/absolute/path/ToiDoneServer`を実際の場所に置き換えます。キーは設定に含めません。

```json
{
  "mcpServers": {
    "toidone": {
      "command": "/absolute/path/ToiDoneServer/.venv/bin/toidone-mcp",
      "args": []
    }
  }
}
```

独自のCLI設定を使う場合は`args`に`["--config", "/absolute/path/config.json"]`を指定します。MCPの標準出力にはプロトコル以外を出しません。

`list_tasks`、`get_task`、`create_task`、`update_task`、`complete_task`、`reopen_task`、`delete_task`、`restore_task`と、プロジェクト操作、認証確認、snapshot/差分取得を提供します。更新には`expected_revision`が必須です。削除はプレビューが既定で、実行には`confirm=true`と`dry_run=false`の両方が必要です。

## 検証

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
```

テストは一時DBと専用UUIDを使います。結合テストは実際のHTTPサーバー、CLIプロセス、公式MCPクライアントとstdioサーバーを起動します。[検証記録](docs/verification.md)に実際の結果を記載します。iPhone実機の双方向反映をこれらのテストで証明したとは扱いません。

[API契約](docs/api.md) · [Debianへの配置とバックアップ](docs/self-hosting.md) · [MVPの進捗](docs/mvp.md)

## MyToDoからの改名

パッケージは`toidone-server`、Python moduleは`toidone`、コマンドは`toidone`、
`toidone-server`、`toidone-admin`、`toidone-mcp`です。旧`mytodo*`コマンドも互換aliasとして使えます。
MCPクライアントの起動パスは上記のToiDoneServerの例に更新してください。

新規環境のCLI設定は`~/.config/toidone/config.json`、サーバーデータは
`~/.local/share/toidone-server/toidone.sqlite3`です。新しい既定パスがない場合は既存の
`~/.config/mytodo/config.json`と`~/.local/share/mytodo-server`を使い、指定したdata-dirに
`mytodo.sqlite3`があればそのDBを継続して開きます。既存のキー・失効状態・タスクを引き継ぎ、
DBやWALを自動で移動しません。新旧両方がある場合は新名を優先します。

OS keyringも新名を優先し、キーが未登録の場合だけ旧`mytodo:`サービスを読み取ります。
既存キーの形式とAPI v1の契約は継続します。`--config`と`--data-dir`の明示指定も利用できます。
