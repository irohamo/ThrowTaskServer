# 検証記録

## GitHub公開 — 2026-11-16 JST

公開先は[ToiDoneServer](https://github.com/irohamo/ToiDoneServer)、default branchは`main`です。サーバー・CLI・MCP・共通client・関連文書の履歴を公開対象とし、アプリのソースは含めません。

CIはpushとpull requestで固定済み依存関係のインストール、ruff check/format、pytestを実行します。結果は[GitHub Actions](https://github.com/irohamo/ToiDoneServer/actions/workflows/test.yml)を参照してください。

## ToiDoneへの改名 — 2026-11-16 JST

| 環境 | 結果 |
| --- | --- |
| macOS / Python 3.12.13 | pytest 19件成功、ruff check/format成功 |
| Debian 13.7実機 / Python 3.13.5 | pytest 19件成功、ruff check/format成功、新名の4コマンド起動成功 |
| 実リポジトリの配布設定 | `toidone-server`のwheel/sdistビルド成功、新旧8コマンドの起動成功 |

`toidone` moduleで実HTTP・CLI・stdio MCPの既存結合テストを再実行しました。
追加3件で旧名の設定探索、DB内のタスクと失効状態の保持、keyringの探索順序を確認しました。
keyringの探索順序はmockでの検証で、利用者の実Keychainは操作していません。
Debianでは専用の一時ディレクトリを使い、終了後に削除しました。

アプリ側はToiDone project / schemeでiOSシミュレーター向けとmacOS向けのビルドに成功し、
生成されたInfo.plistの表示名・製品名・実行名がToiDone、Bundle IDが`com.irohamo.toidone`であることを確認しました。
macOSは`CODE_SIGNING_ALLOWED=NO`でビルドしています。Swift保存処理と実HTTP同期も再実行し、
同じUserDefaults domainへ取り込んだ旧snapshotの読込、未送信キュー、破損データ保持を確認しました。
新Bundle IDはOS上で旧アプリとは別扱いになるため、既存インストールからの自動移行を証明するものではありません。
新Bundle IDのApple側登録・署名・TestFlightアップロード、iPhone実機検証は未実施です。

以下は改名前の実装検証です。

## 初回実装 — 2026-11-15 JST

## サーバー・CLI・MCP

| 環境 | 結果 |
| --- | --- |
| macOS / Python 3.12.13 | pytest 16件成功、ruff check/format成功 |
| Debian 13.7実機 / Python 3.13.5 | pytest 16件成功、ruff check成功 |
| Python配布パッケージ | wheel/sdistのビルド成功 |

実際のDebian実機へSSHし、専用の一時ディレクトリで固定済み依存関係をインストールして検証しました。終了時にその環境を削除しています。常駐サービスの設置はしていません。

結合テストは実HTTPサーバーとCLIプロセスを起動し、公式MCP SDKクライアントからstdio MCPサーバーへ接続します。新方式とlegacy方式の両方で、CLI作成→MCP完了→CLI確認を実行しました。競合の拒否、削除プレビュー、論理削除/復元、キー失効後にCLIとMCPの書き込みが停止することも確認しています。

APIのトランザクション競合、UUID作成の冪等性、入力値をエラーへ反射しないこと、差分ページング、月末/閏年の繰り返し、編集済み次タスクの保持、移行済み次タスクの重複防止、繰り返し上限時のrollback、バックアップ復元を確認しました。

## 別管理のアプリ側

- iOSシミュレーター向けとmacOS向けビルド成功。起動/UI操作とiPhone実機の証拠ではありません。
- 既存のSwift保存処理チェック成功。
- アプリと共通のSwift同期コードをmacOS上の検証プログラムから実HTTP APIへ接続し、未送信変更の再起動後復旧、競合時の保持、サーバーでの繰り返し生成、結果不明の作成のUUID確認、論理削除、失効時停止を確認。
- これらのテストは専用UserDefaultsと一時DB・専用キーを使い、既存ユーザーデータとユーザーのKeychainに接続していません。

## 残っている確認

- HTTPS経由でのiPhone実機とDebianの双方向反映。
- iPhone上でのオフライン復旧、Keychain保存、同期設定画面の操作。
- OS keyringを使うDebian常駐クライアントの接続。今回の実接続テストは明示的な0600資格情報ファイルを使用。
- systemdの継続運転、HTTPSリバースプロキシ、運用バックアップの配置。
- App Store配布。

MVP全体の受け入れ条件がすべて通過したとは扱いません。
