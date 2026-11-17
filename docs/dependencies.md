# 先に終えるタスクとブロック理由

0.4.0以降で、タスクに`dependencyIDs`（最大64個のUUID）と`blockReason`（任意、最大2000文字）を指定できる。
同じユーザーのタスクだけを参照できる。新しいリンクは存在する未削除タスクが対象で、自己依存・重複・循環依存は拒否する。更新にはこれまでどおりrevisionが必要。

## 状態

`state`は従来の手動状態（active / waiting / blocked）を維持する。`blockReason`は手動のblockedを説明する文で、理由だけでは状態を変更しない。

APIのget / list / snapshot / 作成・更新応答には、読取専用の`effectiveState`と`blockingDependencyIDs`を返す。未完了または参照できない依存先が1つでもあると`effectiveState=blocked`になる。すべて完了すると手動状態へ戻るため、手動のwaitingやblockedは自動解除しない。
`list_tasks(state=...)` / `GET /v1/tasks?state=...` / CLIの`--state`検索は`effectiveState`で絞り込む。

依存先を完了・再開・削除・復元すると、読取時の実効状態に反映する。実効状態の変化だけでは対象タスクの本文・revision・更新時刻を書き換えない。
`changes`は直接編集されたレコードの保存履歴を返す。依存先の変更後はsnapshot / list / getを読み直して実効状態を更新する。

タスクの完了は独立した明示操作として許可する。依存先の完了が依存するタスクを自動完了させることはない。
削除した依存先を無条件に完了扱いにしない。既存のリンクは保持し、依存するタスクの他フィールドは引き続き編集できる。リンクを外すか依存先を復元して解消する。
削除プレビューの`blockedDependentTaskIDs`は、影響を受ける同じユーザーの未完了タスクを示す。
繰り返しの次回タスクは同じ依存先を引き継ぐ。依存先自体の次回タスクへの自動付け替えは行わない。

## CLI

```sh
throwtask tasks create "提出する" --depends-on <資料を作るタスクUUID> --json
throwtask tasks edit <対象UUID> --revision <現在値> \
  --depends-on <先行UUID1> --depends-on <先行UUID2> --json
throwtask tasks list --state blocked --json
throwtask tasks edit <対象UUID> --revision <現在値> --clear dependencyIDs --json
throwtask tasks edit <対象UUID> --revision <現在値> \
  --state blocked --block-reason "必要な権限がない" --json
throwtask tasks edit <対象UUID> --revision <現在値> --state active --clear blockReason --json
```

`--depends-on`はリスト全体の置換。複数指定でき、`--clear dependencyIDs`で空にする。競合時に自動で上書き・再送しない。

## MCP

既存の17 toolsを使い、`create_task(task={..., "dependencyIDs":["..."]})`、
`update_task(task_id="...", expected_revision=..., changes={"dependencyIDs":[]})`で設定する。
手動で止める場合は`state=blocked`と`blockReason`を指定する。認証情報を引数へ渡す必要はない。

## 検証と互換性

SQLite schema 2 / API v1を維持し、旧レコードは依存先なし・ブロック理由なしとして読む。旧createの再確認も、元の作成fingerprintとの互換性を保つ。
新しいフィールドを保存した後、0.3.x以前へのコードだけの差し戻しは非対応。更新ツールは停止前に`TARGET_DATA_FORMAT_UNSUPPORTED`で拒否する。[復旧手順](upgrading.md)を使う。

一時DBで複数依存先、完了・再開、手動状態の維持、削除・復元、循環、同時の逆向き更新、他ユーザーの遮断、revision競合、旧データ互換を検証する。実HTTP / CLI / 公式MCP SDKで横断した操作も確認する。
