# ThrowTask Server

- 公開先は[https://github.com/irohamo/ThrowTaskServer](https://github.com/irohamo/ThrowTaskServer)、default branchは`main`。アプリのソースを持ち込まない。

- 製品名はThrowTask（スロータスク）、公開用リポジトリはThrowTaskServer。Python package/moduleと現行コマンドは`throwtask`系。旧名は保存データ・資格情報・コマンドの互換性を保つ箇所だけに残す。
- 個人利用向けのセルフホストToDoを作る。MVPはToDo API、Debian CLI、MCPサーバー、別管理のThrowTaskアプリとの接続。MCPはMVPに含める。
- MVPと受け入れ条件は [docs/mvp.md](docs/mvp.md)、検証結果は [docs/verification.md](docs/verification.md) を参照する。API・CLI・stdio MCPとテストを実装済み。iPhone実機と常駐運用の完了は別に確認する。
- セットアップは `uv sync --frozen`、検証は `uv run ruff check src tests deploy`、`uv run ruff format --check src tests deploy`、`uv run pytest -q`。既存の配置の更新は[更新手順](docs/upgrading.md)に従い、DBを自動で巻き戻さない。
- CLIとMCPは同じAPIクライアントを使う。業務ルール、認証、データ検証はAPI側に集約し、CLIやMCPからDBを直接書き換えない。
- 更新・削除はrevision等による条件付き更新を使う。強制上書き、強制削除、認証失敗時の自動再試行を実装しない。
- 認証はAPIキーのみ。端末・botごとに発行し、管理者が利用者を作成する。パスキー・招待・復旧コード・セッション認証は廃止。既存キーとユーザーごとの所有者・差分カーソルを維持する。
- 認証失効時は書き込みを停止する。APIキー等の秘密をログ、Git、会話、MCP toolの引数や結果へ出さない。
- 削除に書き込みなしのプレビューと明示指定を設ける。実接続テストで削除するのは検証専用UUIDだけ。
- Debian実接続、実際のMCPクライアント接続、iPhone双方向反映、認証失効、オフライン復旧、バックアップ復元の実測をモックやビルド成功で代用しない。
- このリポジトリの公開対象はサーバー、CLI、MCP、共通クライアント、関連文書。アプリのソース、署名設定、既存CloudKit PoCの秘密や個人データを持ち込まない。
- 既存の利用者の変更を保存し、コミット対象は作業スコープだけにする。
