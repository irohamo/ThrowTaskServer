# MyToDo Server

- 個人利用向けのセルフホストToDoを作る。MVPはToDo API、Debian CLI、MCPサーバー、別管理のMyToDoアプリとの接続。MCPはMVPに含める。
- MVPと受け入れ条件は [docs/mvp.md](docs/mvp.md)、検証結果は [docs/verification.md](docs/verification.md) を参照する。API・CLI・stdio MCPとテストを実装済み。iPhone実機と常駐運用の完了は別に確認する。
- セットアップは `uv sync --frozen`、検証は `uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run pytest -q`。
- CLIとMCPは同じAPIクライアントを使う。業務ルール、認証、データ検証はAPI側に集約し、CLIやMCPからDBを直接書き換えない。
- 更新・削除はrevision等による条件付き更新を使う。強制上書き、強制削除、認証失敗時の自動再試行を実装しない。
- 認証失効時は書き込みを停止する。APIキー等の秘密をログ、Git、会話、MCP toolの引数や結果へ出さない。
- 削除に書き込みなしのプレビューと明示指定を設ける。実接続テストで削除するのは検証専用UUIDだけ。
- Debian実接続、実際のMCPクライアント接続、iPhone双方向反映、認証失効、オフライン復旧、バックアップ復元の実測をモックやビルド成功で代用しない。
- このリポジトリの公開対象はサーバー、CLI、MCP、共通クライアント、関連文書。アプリのソース、署名設定、既存CloudKit PoCの秘密や個人データを持ち込まない。
- 既存の利用者の変更を保存し、コミット対象は作業スコープだけにする。
