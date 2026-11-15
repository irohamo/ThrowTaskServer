# MCPサーバー

MVPに含むMCP adapterの置き場です。CLIと同じAPIクライアントを使い、タスクの一覧・取得・作成・更新・完了・再開・削除をtoolsとして提供する予定です。

初期transport候補はstdio。認証情報はtoolの引数・結果に含めず、設定した秘密ストアから取得します。

[実装](../src/mytodo/mcp_server.py)を参照してください。`uv run mytodo-mcp`でstdio起動します。17 toolsを提供し、CLIと同じ接続設定を使います。依存関係は`uv.lock`で固定しています。
