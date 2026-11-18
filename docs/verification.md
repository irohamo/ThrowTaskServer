# 検証記録

## APIキー専用化 — 2026-11-18（0.5.0）

- ローカルのruff check / formatとpytest **79件**が成功。
- 既存キー・ユーザー分離・読み取り専用キー・失効を確認。旧セッションのDB行が残っていてもAPI認証を拒否し、旧認証endpointとAASAが404になることを確認。
- 管理者によるユーザー作成と端末別キー発行、キーを表示しない管理出力、0600ファイルを確認。
- schema 1の移行・旧レコード・指紋・カーソル・キー・バックアップ復元の検証を維持。schema 2のデータは削除しない。
- 別管理アプリの7つのSwiftチェックと実HTTP同期が成功。APIキーでの接続、所有者不一致の送信停止、利用者切替での未送信変更保持、失効キーの拒否を確認。利用者のKeychainは操作しない。

この段階では新しい版の稼働反映・iPhone実機・TestFlightでのAPIキー保存と双方向同期は未確認。以下のパスキーに関する記載は廃止前の履歴であり、現行の認証方法ではない。

## 依存タスク — 2026-11-17 JST（0.4.0の公開前検証）

- macOS / Python 3.12.13でpytest 89件成功、ruff check/format成功。
- 複数の先行タスク、完了・再開による実効状態、手動waiting/blockedと理由の維持、依存先の削除・復元・リンク解除を確認した。実効状態だけの変化では依存するタスクのrevisionを変更しない。
- 自己依存・循環・重複・別ユーザーの参照を拒否し、同時の逆向き更新でも循環を作らない。既存の参照できないリンクは保持して他項目を編集できる。
- 実HTTP・CLI・公式MCP SDKで依存先の設定、blocked検索、先行タスクの完了、手動理由の設定・消去を横断して確認した。
- schema 2 / API v1と旧作成fingerprintを維持し、依存関係を含むデータから0.3.x以前へのコードだけの差し戻しを更新ツールが停止前に拒否することを確認した。
- 別管理アプリの7つのSwiftチェックと実HTTP同期に成功。依存順の送信、永続キューの再起動、Siri／Watch向け状態、競合時の保持、参照先が消えた書き込みの拒否後に修正して同期できることを確認した。専用データのみを使い、利用者のKeychainは操作していない。

この時点ではDebian実機・常駐APIへの配置、iPhone／Watchでの画面操作は未確認。旧版のビルド・実機記録を今回の検証の代わりにはしない。[依存関係の仕様](dependencies.md)。

## ユーザー・パスキー認証 — 2026-11-17 JST（0.3.0の公開前検証）

- macOS / Python 3.12.13でpytest 79件成功、ruff check/format成功。実HTTP・CLI・stdio MCPの既存動作を含む。
- ES256のソフトウェア認証器による実署名で登録・ログインを検証。origin/RP/UV/userHandle不一致、期限切れ・消費済みchallengeを拒否した。
- legacy所有者の明示的な招待、招待JSON/QRの0600保存、秘密を含めない管理コマンド出力を確認した。
- 同じUUID・外部IDを別ユーザーで扱えること、プロジェクト参照・削除・差分カーソルの分離、読み取り専用キーの変更拒否を確認した。
- セッション期限、更新ローテーション・再利用時失効、ログアウト、復旧による旧パスキー/セッション失効、古い復旧challengeの無効化を確認した。
- スキーマ1のDBを実際に作成して2へ移行し、UUID・revision・fingerprint・旧カーソル・繰り返しリンク・既存キー・backup/restoreを確認した。
- 別管理アプリの6つのSwiftチェックと実HTTP同期が成功。アカウント別outboxの保持、別ユーザーへの送信停止、セッション更新/ログアウト、結果不明の更新後に自動再送しない動作を確認した。専用UserDefaultsと注入した秘密ストアだけを使い、利用者のKeychainは操作していない。
- 別管理アプリはiOS Simulator・macOS向けビルド成功（署名なし）。APIのHTTPS IP/ポートとRPドメインを分ける設定を確認した。

認証ドメインは未設定。AppleとのAssociated Domainsの成立、ネイティブパスキー・Face ID/iCloud Keychain、QRカメラ、実Keychainの画面操作、iPhone/Watch実機、Debianでの新依存の実測は未確認。この検証時点では稼働サーバーの更新・公開タグの作成は行っていない。[認証手順](authentication.md)。

## ThrowTaskへの改名 — 2026-11-17 JST

- macOS / Python 3.12.13でpytest 64件成功。実HTTP、CLI、stdio MCPと、旧名の設定・DB・keyring探索順序・Python起動互換を確認。
- 一時Git / SQLiteとsystemd境界のfakeで、旧ToiDone / MyToDoの起動名とDBを保持した更新を確認。旧コマンドを提供しない版や新DB名を開けない版は停止前に拒否。
- 固定lockのまま`uv sync --frozen --offline`成功。利用者のDB・設定・Keychain・常駐サービスには接続していない。
- 別管理アプリの5つのSwiftチェックが成功。同じUserDefaults domain内の旧データ・未送信キュー・Watch受領記録・認証停止・競合を保持し、旧URLも受け付ける。Keychain照会はfakeのみ。
- 改名後のDebian実機、常駐サービスの更新、新Bundle IDでのアプリ全体のビルド・署名・iPhone/Watch動作は未確認。

以下の2026-11-16以前の記録は当時のToiDone / MyToDo名での結果を保存したもの。

## バージョンアップ方法 — 2026-11-16 JST

- macOS / Python 3.12で全52件のpytest、ruff check/formatに成功。
- 0.2.0の更新ツールと手順を追加し、公開タグを`v0.2.0`とする。タグが指すcommitの[CI結果](https://github.com/irohamo/ThrowTaskServer/actions/workflows/test.yml)を確認してから常駐APIへ適用する。常駐APIの更新は別途実施する。
- macOSの一時Git/SQLiteとサービス境界のfakeで、計画のみ・未保存変更の拒否・パスの別名・更新成功・各段階の失敗・DBを自動復元しない動作を確認。旧0.1系への適用は停止前に拒否する。
- 追加の一時環境で0.1.0→0.2.0を実行。実際のuv・Git・SQLite backup/restore・HTTP API・CLI・stdio MCPで、停止時バックアップ、起動版の確認、taskのrevision、キーと失効状態、CLI設定の保持とMCP再接続に成功。systemdとOSユーザー切り替えだけは検証プロセス用adapterに置き換えた。
- hp-i3 / Debian上では標準ライブラリだけの更新プログラムで計画表示を実行し、稼働中のサービス・パス・配置commitを正しく取得した。`applied=false`で、稼働中のソース・DB・サービスは変更していない。
- `--version`の4コマンド、API/OpenAPI/package/lockの版の一致を確認。`uv lock --offline --check`成功。

実systemdでの切り替え、Debianでの依存更新、iPhone実機は別途確認する。[更新と復旧手順](upgrading.md)。

## bot向け設定・識別・状態・時刻差分 — 2026-11-16 JST

- macOS上でpytest 38件成功、ruff check/format成功。
- 一時DBの実HTTP API・CLIプロセス・公式MCP SDKのstdioクライアント（auto/legacy両方式）で、チェックリストと繰り返しの設定・更新、外部IDの重複拒否と既存IDの返却、状態の設定・検索、since差分のページングを確認。
- 同じ外部IDの同時作成、条件付きの外部ID変更・消去、削除済みIDの予約、次回タスクの外部ID消去、既存DB・旧作成fingerprintの互換性を確認。
- 同じ時刻の境界を含む差分、UTC offset正規化、削除履歴、空の差分からのcursor再開を確認。
- 別管理のSwift保存・同期コードから実HTTP APIへ接続し、新フィールドの再起動後保持、アプリ編集後の保持、待ち状態のToday除外と通常状態への復帰を確認。利用者のKeychain、既存DB、常駐サービスは操作していない。

この変更のDebianでの実測、常駐APIへの配置、iPhone実機での確認は未実施。以前の版のDebian検証結果を今回の証拠には使わない。[利用例](bots.md)。

## GitHub公開 — 2026-11-16 JST

当時の公開名はToiDoneServer。改名後の公開先は[ThrowTaskServer](https://github.com/irohamo/ThrowTaskServer)、default branchは`main`です。サーバー・CLI・MCP・共通client・関連文書の履歴を公開対象とし、アプリのソースは含めません。

CIはpushとpull requestで固定済み依存関係のインストール、ruff check/format、pytestを実行します。結果は[GitHub Actions](https://github.com/irohamo/ThrowTaskServer/actions/workflows/test.yml)を参照してください。

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
