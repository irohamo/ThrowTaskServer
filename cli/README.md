# Debian CLI

共通APIクライアントを使ってToDo APIを操作します。JSON出力、構造化エラー、削除プレビューと明示指定に対応しています。

チェックリストは`--checklist-item`、共通の繰り返しは`--repeat`/`--repeat-interval`/`--repeat-anchor`で設定できます。チェック項目の完了や独立した繰り返しなど、全フィールドは`--data <JSON file>`（`-`で標準入力）から設定できます。編集には読んだ`--revision`が必要です。

`--external-id`/`--source`で外部IDと作成元、`--state active|waiting|blocked`で状態を設定・絞り込みできます。`changes --since <UTC offset付き時刻>`で時刻以降の変更を取得します。[bot向けの例と更新規則](../docs/bots.md)を参照してください。

[実装](../src/throwtask/cli.py)を参照してください。`uv run throwtask --help`で操作を確認できます。[セットアップ](../README.md)後に利用してください。
