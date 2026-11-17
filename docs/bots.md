# botから使う

2026-11-16の要望順に、チェックリスト・繰り返し、外部ID・作成元、待ち/ブロック状態、時刻からの差分取得を扱います。API・CLI・MCPは同じ検証と条件付き更新を使います。セットアップと資格情報の保存は[README](../README.md)を参照してください。

## 1. チェックリストと繰り返し

CLIでは各項目と共通の繰り返しを指定できます。

```bash
throwtask tasks create "週次レビュー" --when 2026-11-16 \
  --checklist-item "予定を確認" --checklist-item "次週を計画" \
  --repeat week --repeat-interval 1 --source briefing-bot --json
```

`--checklist-item`はチェックリスト全体を新しい未完了項目に置き換えます。既存の項目を完了・並べ替え・一部変更するときは、取得した項目UUIDを保った配列を`--data`で送ります。`--repeat`は共通の繰り返しに切り替えます。`--repeat-anchor YYYY-MM-DD`は月末などの基準日を保持します。繰り返しの生成には予定日または締切が必要です。

MCPの`create_task`の引数例です。`id`を事前に決めて保持すると、通信断後にも同じUUIDで確認できます。

```json
{
  "task": {
    "id": "b3fb8eac-0741-4bc5-961d-17be2c8a1bb1",
    "title": "週次レビュー",
    "scheduledDay": "2026-11-16",
    "sharedRepeat": {"unit": "week", "interval": 1},
    "checklist": [
      {"id": "69e92a40-a68d-444e-8b73-c60535895449", "title": "予定を確認"},
      {"id": "08779c5b-a052-46dc-a1d5-4fab526b2b6a", "title": "次週を計画"}
    ],
    "source": "briefing-bot",
    "externalID": "routine:weekly-review"
  }
}
```

`get_task`で読んだrevisionが1の場合、`update_task`で最初の項目を完了します。配列全体を送り、残す項目のUUIDも含めます。

```json
{
  "task_id": "b3fb8eac-0741-4bc5-961d-17be2c8a1bb1",
  "expected_revision": 1,
  "changes": {
    "checklist": [
      {"id": "69e92a40-a68d-444e-8b73-c60535895449", "title": "予定を確認", "isCompleted": true},
      {"id": "08779c5b-a052-46dc-a1d5-4fab526b2b6a", "title": "次週を計画", "isCompleted": false}
    ]
  }
}
```

`unit`はday/week/month/year、`interval`は1〜10000です。独立した周期は`usesSeparateRepeats=true`と`scheduledRepeat`/`deadlineRepeat`で設定し、共通ルールと同じ形式を使います。CLIでも`--data`で同じフィールドを渡せます。チェックリストの消去は`checklist=[]`または`--clear checklist`、繰り返しの消去は該当ルールをnullまたは`--clear sharedRepeat`などで指定します。

完了時にサーバーが次回を一度だけ作り、チェック項目を未完了に戻します。次回の`source`は保持し、`externalID`はnullにします。

## 2. 外部IDと作成元

`externalID`は外部対象の識別子、`source`は投入したbotの名前です。例えば外部IDを`github:irohamo/ThrowTaskServer#42`、作成元を`github-bot`にします。メールIDにはアカウントやメールボックスの名前空間も含めます。APIキーや認証URLを入れません。

```bash
throwtask tasks create "issue #42を確認" \
  --external-id 'github:irohamo/ThrowTaskServer#42' --source github-bot --json
throwtask tasks list --external-id 'github:irohamo/ThrowTaskServer#42' --json
```

MCPでは`create_task`/`update_task`の`externalID`/`source`を使い、`list_tasks(external_id=..., source=...)`で完全一致検索します。事前の検索と同時に別botが登録しても、APIのトランザクションとDBの一意制約が二重作成を拒否します。

重複は`EXTERNAL_ID_ALREADY_EXISTS`と`existingRecordId`を返します。既存タスクを読み直して扱いを判断し、自動で上書き・再送しません。削除済みタスクもIDを予約するため、`get_task(include_deleted=true)`またはCLIの`get --include-deleted`で確認します。復元や外部IDの消去にも現在のrevisionが必要です。作成元は自己申告のメタデータで、認証主体の証明には使いません。

## 3. 待ち・ブロック中

`state`をactive（通常）、waiting（結果待ちなど）、blocked（依存作業などで着手できない）に分けます。省略時はactiveです。完了とSomedayは別フィールドで、待ち状態のまま完了・再開もできます。

```bash
throwtask tasks create "CAの結果待ち" --state waiting --source career-bot --json
throwtask tasks list --status open --state waiting --json
throwtask tasks list --status open --state active --json
throwtask tasks edit <uuid> --revision <observed-revision> --state active --json
```

MCPでは`create_task(task={..., "state":"waiting"})`、`update_task(..., changes={"state":"blocked"})`、`list_tasks(status="open", state="active")`を使います。`status=open`だけでは待ち/ブロックも含みます。

別管理のアプリはWaiting/Blockedリストを持ち、待ち/ブロックをToday、Siriの今日一覧、Watch/Widget用の着手可能な一覧から外します。プロジェクトと検索では引き続き確認できます。

依存先の指定と、手動状態を保持した実効状態の計算は[依存関係](dependencies.md)を参照。

## 4. 時刻以降の変更

```bash
throwtask changes --since '2026-11-16T00:00:00+09:00' --limit 100 --json
```

MCPでは`list_changes`へ次の引数を渡します。

```json
{"since":"2026-11-16T00:00:00+09:00", "limit":100}
```

タスクとプロジェクトの作成・更新・削除・復元、繰り返し生成を変更順に返します。同じレコードの複数の変更も含みます。UTC offsetは必須で、指定時刻と同時の変更も含めます。`hasMore=true`なら同じ`since`を保って、返された`cursor`を`after`に指定します。

朝のブリーフが全ページの処理に成功した後、最後の`cursor`を保存します。次回は`list_changes(after=<cursor>)`または`throwtask changes --after <cursor>`にすると、前回処理済みの時刻境界の重複を避けられます。失敗した途中のcursorでチェックポイントを確定しません。サーバーの再作成やバックアップ復元では[snapshotからの再同期](api.md)を判断します。

## 既存環境への反映

サーバー更新後の初期化で外部IDの一意インデックスを追加します。既存のJSONレコードに新フィールドがなくても読め、過去に作成したUUIDの冪等性と端末別キーの失効状態を保持します。アプリを更新する場合は先にサーバーへこのAPI拡張を反映してください。稼働中サーバーやiPhoneへの反映は、コード・ローカル検証とは別に確認します。
