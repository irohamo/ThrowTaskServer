# MCPサーバー

CLIと同じAPIクライアントを使い、タスクの一覧・取得・作成・更新・完了・再開・削除をtoolsとして提供します。

transportはstdio。認証情報はtoolの引数・結果に含めず、設定した秘密ストアから取得します。

`create_task`/`update_task`の`checklist`と繰り返しフィールドから設定できます。`externalID`/`source`、`state=active|waiting|blocked`、`list_changes(since=...)`も同じAPIの入力制約と競合ルールを使います。[具体的なtool引数](../docs/bots.md)を参照してください。

[実装](../src/toidone/mcp_server.py)を参照してください。`uv run toidone-mcp`でstdio起動します。17 toolsを提供し、CLIと同じ接続設定を使います。依存関係は`uv.lock`で固定しています。
