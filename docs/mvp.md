# MVP

2026-11-15 JSTに決めた方針です。まず本人が使えることを優先し、サーバー・CLI・MCPと共通クライアントを公開対象として開発します。アプリのソースは別管理です。

## 構成

```text
ToiDoneアプリ -> アプリ用API adapter ----------------+
Debian CLI -> 共通API client -----------------------+-> ToDo API -> 永続ストア
MCP client -> MCP adapter -> 共通API client ---------+
```

1インスタンス1利用者を設計の出発点とします。CLIとMCPの操作は同じAPI、データモデル、認証・競合ルールを使います。

## 最初の実装単位

1. 最小のタスク契約とAPIを決める。
2. 認証と条件付き更新を備えたAPIを実装する。
3. 共通APIクライアントからCLIとMCPを接続する。
4. CLIで作成、MCPで完了、CLIで確認する。
5. Debian実環境とiPhoneで同じUUIDのタスクを相互に操作する。

## MVPの完了条件

- タスクの一覧・取得・作成・更新・完了・再開・削除をCLIとMCPから実行できる。
- 実際のMCPクライアントでtoolsの一覧と呼び出しを確認できる。
- iPhoneで作成・編集した値をCLIとMCPから取得でき、逆方向の変更もiPhoneで確認できる。
- 古いrevisionによる更新・削除を拒否し、競合したデータを保持する。
- 削除のプレビューは書き込まず、削除実行には明示指定が必要。
- 認証を手動失効させるとCLIとMCPが書き込みを停止する。
- iPhoneのオフライン編集から復旧できる。
- サーバーのバックアップから復元できる。
- 起動、CLI/MCP接続、バックアップと復元の手順を再現できる。

実接続・実機・失効・復元の検証結果を、モックテストやビルド成功で代用しません。

## 採用した構成

- Python 3.12以上、FastAPI、SQLite。API v1とJSON envelope v1。
- タスクとプロジェクトの共通モデル。予定日・締切・タグ・チェックリスト・Someday・繰り返しに対応。[API契約](api.md)を参照。
- 期限なし・手動失効可能な端末別APIキー。ハッシュをDBに保存し、要求ごとに失効を確認。
- 公式MCP Python SDK 2.3.0、stdio transport。共通API clientをCLIと共有。依存関係はuv.lockで固定。
- snapshotと差分履歴、条件付き更新、tombstone、復元、UUIDによる作成結果確認。
- アプリ側は永続outbox、再起動後復旧、競合データの保持、Keychain、初回の既存UUID移行を実装。

## 進捗

- ローカルGitリポジトリとディレクトリ構成を作成。
- API・CLI・MCPを実装。2026-11-16のToiDoneへの改名後、macOSとDebian実機で19件のテストに成功。
- 別管理のアプリに接続設定と同期処理を実装。iPhone実機での受け入れは未通過。
- GitHubリポジトリは[ToiDoneServer](https://github.com/irohamo/ToiDoneServer)として公開し、`main`で管理する。[検証済みと未検証の範囲](verification.md)を参照。
