# MyToDo Server

個人利用向けのセルフホストToDoサーバーと、Debian CLI・MCPサーバーのリポジトリです。MyToDoアプリと同じデータを操作する構成を目指します。

現在はリポジトリの雛形とMVP要件を用意した段階です。実行可能なサーバー・CLI・MCPはまだありません。

## 構成

| ディレクトリ | 役割 |
| --- | --- |
| [server/](server/README.md) | 認証、データ検証、タスク操作、永続化を担当する共通API |
| [client/](client/README.md) | CLIとMCPで共有するAPIクライアント |
| [cli/](cli/README.md) | Debianから使うCLIとJSON出力 |
| [mcp/](mcp/README.md) | AIクライアント向けのMCP adapter |
| [docs/](docs/mvp.md) | MVP要件と検証項目 |

iOS/macOSアプリのソースは別リポジトリで管理します。

## 最初に通す経路

CLIでタスクを作成し、MCPで完了し、CLIで結果を確認します。同じAPIを通し、認証・入力制約・競合処理を共有します。その後、Debian実環境とiPhoneで双方向反映を検証します。

実装言語、DB、認証方式、SDKとAPI契約は設計時に確定します。セットアップ手順とテストコマンドは実行できる実装と合わせて追加します。

[MVPと完了条件](docs/mvp.md)を参照してください。
