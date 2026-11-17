# Changelog

## Unreleased — ThrowTaskへの改名（2026-11-17）

- 製品・リポジトリ・Python package/module・CLI/MCP・新規systemd unitをThrowTask名へ変更。
- 旧ToiDone / MyToDoコマンド、既存設定・DB・keyring、旧ToiDone Python起動方法を継続利用。
- 更新プログラムが旧systemd配置を読み、旧管理コマンドでバックアップできるよう対応。
- 既存DB・認証キー・API v1は変更しない。既存の`v0.2.0`タグはToiDone版の記録として保持する。

## 0.2.0 — 2026-11-16

- CLIからチェックリストと繰り返しを設定するオプションを追加。
- タスクに外部ID・作成元・通常/待ち/ブロック状態を追加。外部IDの二重登録を拒否。
- API・CLI・MCPの変更履歴にUTC offset付き`since`を追加。
- Debian/systemd向けの、事前検証・バックアップを伴う更新コマンドを追加。
- CLI・API・管理・MCPの`--version`、health応答の製品版番号を追加。

API契約はv1、SQLite schemaは1を継続します。既存レコードとキーを保持して前方更新できます。
新フィールド保存後の0.1.0へのコードだけの差し戻しは非対応です。[更新と復元](docs/upgrading.md)を参照してください。

## 0.1.0

- セルフホストAPI、共通クライアント、Debian CLI、stdio MCP。
- 条件付き更新、snapshot/差分履歴、チェックリスト、繰り返し、バックアップ/新規復元。
- ToiDoneへの改名と旧MyToDoの保存先・資格情報・コマンドとの互換性。
