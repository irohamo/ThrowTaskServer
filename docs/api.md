# API v1

起点は`/v1`。`/docs`と`/openapi.json`には生成された契約があります。`/healthz`とAPI文書以外は`Authorization: Bearer <API key>`が必要です。APIキーをURLに含めません。

成功: `{"version":1,"ok":true,"data":...}`。エラー: `{"version":1,"ok":false,"error":{"code":"..."}}`。入力エラーに入力値を含めません。

| 操作 | エンドポイント |
| --- | --- |
| 一覧・検索 | `GET /tasks`、`GET /projects` |
| 作成 | `POST /tasks`、`POST /projects` |
| 取得・変更・論理削除 | `GET` / `PATCH` / `DELETE /tasks/{uuid}`、`/projects/{uuid}` |
| 削除プレビュー | `GET /tasks/{uuid}/delete-preview`、`/projects/{uuid}/delete-preview` |
| 復元 | `POST /tasks/{uuid}/restore`、`/projects/{uuid}/restore` |
| 一括取得・差分取得 | `GET /snapshot`、`GET /changes?after=0&limit=100` |
| 認証確認 | `GET /auth/status` |

## レコード

公式CLI/MCPはUUIDを送信前に生成し、結果不明時にも同じUUIDを返します。HTTP APIはUUIDの省略も受け付けますが、独自クライアントでも送信前の生成を推奨します。

タスクには`title`、`notes`、`scheduledDay`、`deadlineDay`、`deadlineTime`、`deadlineTimeZone`、`projectID`、`tags`、`checklist`、`isCompleted`、`isSomeday`、`sortOrder`、繰り返しフィールドがあります。プロジェクトには`title`、`symbol`、`colorIndex`、`notes`、`sortOrder`があります。詳細と制限はOpenAPIを参照してください。

全レコードに`id`、`revision`、`createdAt`、`updatedAt`、`deletedAt`を返します。日付のみは`YYYY-MM-DD`で保存し、UTC時刻へ変換しません。日時付き締切には日付・`HH:MM`・IANA time zoneを指定します。記録時刻はUTCです。

## 更新と競合

取得・作成・更新の応答はETag `"<revision>"`を返します。更新、削除プレビュー、削除、復元には`If-Match: "<revision>"`が必要です。省略は428、書式不正は400、競合は412/`CONFLICT`と`currentRevision`です。`*`による強制更新はできません。PATCHの省略フィールドは保持し、nullableフィールドのnullは消去です。

繰り返しルール・タグ・チェックリストはフィールド全体を置き換えます。

同じUUID・同じ初回作成内容のPOSTは既存レコードを返し、後の変更を上書きしません。UUIDの内容が違う場合と削除済みUUIDの再作成は409/`ID_ALREADY_EXISTS`です。通信断時は同じUUIDを取得して結果を確認し、競合や認証エラーで自動再送しません。

DELETEには`confirm=true`も必要です。削除済みレコードは通常の一覧/取得から隠れ、`include_deleted=true`で取得できます。プロジェクト削除時はタスクを残して所属を解除し、タスクのrevisionと差分も更新します。プレビューは対象タスクのUUID一覧を返します。プロジェクトを復元しても所属は自動復元しません。

## 一覧と同期

一覧は`q`、`after`、`limit`（1〜500）、`include_deleted`を受け付けます。タスクには`status=all|open|completed`、`project_id`もあります。`items`と`nextCursor`を返し、次ページは`after=nextCursor`で取得します。一覧ページングを同期の代わりに使いません。

`snapshot`は全レコード（tombstone含む）と同じ読み取りトランザクション内の`cursor`を返します。以後は`changes?after=<cursor>`から順序付きの変更を取得し、`hasMore=false`まで進めます。変更とレコード更新は同一トランザクションです。差分履歴のpurgeは実装していません。cursorが現在の上限を超えた場合は409/`CURSOR_INVALID`です。サーバー再作成やバックアップ復元時は、未送信変更を保持してsnapshotからの再同期を判断します。

## 繰り返し

`sharedRepeat`、または`usesSeparateRepeats=true`と`scheduledRepeat`/`deadlineRepeat`を使用します。ルールは`unit=day|week|month|year`、`interval`、`anchorDay`です。月末・閏年のanchorを維持します。

完了への変更時にサーバーが次のタスクを一度だけ生成します。未編集の次タスクは完了解除時に論理削除し、編集済みの次タスクは保持します。アプリ側で二重生成しません。次タスクのチェックリストは未完了へ戻します。日付の繰越しは、日時付き締切のtime zone、なければUTCの今日を基準にします。

MCP/CLI固有の業務ルールはなく、すべて同じAPIを使用します。
