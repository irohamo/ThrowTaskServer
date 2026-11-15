# 共通APIクライアント

Debian CLIとMCP adapterで共有するAPIクライアントの置き場です。認証情報の取得、API呼び出し、構造化エラーの扱いを共通化します。

[共通クライアント](../src/mytodo/client.py)と[資格情報・設定](../src/mytodo/config.py)を実装しています。OS keyringまたは明示した所有者限定ファイルを使い、redirectと自動再試行を行いません。
