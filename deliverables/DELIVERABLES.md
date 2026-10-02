# 成果物一覧

> 2026-09-17 実装開始後の追記：M1（管理モデル・設定保存・非保存ビルド設定）を実装し、自動テスト13件を確認。最新の進捗・検証・次の作業は[実装記録](IMPLEMENTATION_STATUS.md)を参照。以下の「未着手」「実装はまだ開始しない」は設計整理時点の記録であり、今回の実装依頼を制限しない。設計の合意状態は維持する。

更新日：2026-09-18。Pythonパッケージ`cppbuild`、自動テスト、公開APIの利用例を実装済み。現在の完了範囲と検証結果は実装記録を参照。

現行API・要件の本文はSolution／Projectへ統一済み。独立したCMake構成での個別更新、メソッドでのビルド設定受け取りと非保存、種類別の管理設定ファイルを確認済み。全体統合・継承・実行順の具体値・GoogleTest取得方法などの詳細案と区別している。

## 現行の設計成果物

実装開始後の追加資料：

- [DESIGN_REVIEW_2026-10-03.md](DESIGN_REVIEW_2026-10-03.md)：実装全体の設計レビュー。維持する設計、共有ツリー・依存範囲・生成処理の改善案、追加確認結果。提案であり、仕様変更や修正の完了を意味しない。

- [CROSS_PLATFORM_PLAN.md](CROSS_PLATFORM_PLAN.md)：次の開発テーマ。生成器・コンパイラ切り替えのAPI案、判断事項、実装順序、完了条件。

- [CROSS_PLATFORM_REVIEW.md](CROSS_PLATFORM_REVIEW.md)：Windows依存の調査と非Windows対応の方針案。未実装・他OS未検証。

- [USAGE.md](USAGE.md)：GoogleTestのオンライン／オフライン導入、統合利用例、テスト実行手順。

- [MILESTONES.md](MILESTONES.md)：段階別のAcceptance Criteriaと完了状態。
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)：実装範囲・具体化・実検証結果・制約。
- [INTEGRATION_DECISION.md](INTEGRATION_DECISION.md)：M3aの実測結果と、M3bに必須の未合意判断。

| ファイル | 用途 |
| --- | --- |
| [API_DESIGN.md](API_DESIGN.md) | 現行APIの入口・引数・責務と合意状態。API設計の主資料。 |
| [FEATURE_REQUESTS.md](FEATURE_REQUESTS.md) | 要件・採用方針・対象外機能の記録。 |
| [BUILD_DESIGN.md](BUILD_DESIGN.md) | 更新範囲、設定の受け渡し・保存、生成物とcleanの詳細案。追加判断・実検証が必要。 |
| [SETTINGS_DESIGN.md](SETTINGS_DESIGN.md) | ビルド設定非保存・管理設定保存の区分、継承・種類別ファイル・実行順・GoogleTest導入の案。 |
| [DESIGN_NOTES.md](DESIGN_NOTES.md) | 今回の整合整理内容、実装前の判断事項・問題、検証範囲。 |
| [DELIVERABLES.md](DELIVERABLES.md) | 本一覧。資料の用途と現行・参考の区別。 |

仕様を参照するときはAPI_DESIGN.mdを主資料にし、FEATURE_REQUESTS.mdで要件を、DESIGN_NOTES.mdで未決事項を確認する。「再提案」「未確定」は確定仕様ではない。

## 参考資料

| ファイル | 用途・注意 |
| --- | --- |
| [CMAKE_RESEARCH.md](../references/CMAKE_RESEARCH.md) | CMake機能・関連ツールの調査記録。現行APIそのものではない。 |
| [CONFIG_LAYOUT_RESEARCH.md](../references/CONFIG_LAYOUT_RESEARCH.md) | 設定配置の調査。旧配置案を含むため現行仕様は主資料を優先する。 |
| [PROJECT_SETTINGS_RESEARCH.md](../references/PROJECT_SETTINGS_RESEARCH.md) | 設定項目の実装時の参考。候補すべての採用を意味しない。 |
| [API_REVIEW.md](../references/API_REVIEW.md) | 過去のAPI点検・OSS調査。旧APIを含む。 |
| [API_DESIGN_HISTORY_2026-09-16.md](../references/API_DESIGN_HISTORY_2026-09-16.md) | 旧API案と検討経緯の保存。現行仕様として使わない。 |
| [DESIGN_BEFORE_DETAIL_2026-09-17.md](../references/DESIGN_BEFORE_DETAIL_2026-09-17.md) | 今回の詳細整理前のAPI・要件・メモの全文。現行仕様として使わない。 |

README.mdは既存の短いプロジェクト紹介であり、今回の設計成果物には要件を転記していない。
