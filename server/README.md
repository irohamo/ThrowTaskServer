# ToDo API

認証、入力検証、タスク操作、条件付き更新、永続化を担当します。ThrowTaskアプリ・CLI・MCPは同じAPIへ接続します。

[実装](../src/throwtask/server.py)・[ストレージ](../src/throwtask/storage.py)・[API契約](../docs/api.md)を参照してください。起動は`uv run throwtask-server --data-dir ./data`です。
