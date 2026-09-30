# 実行結果（2026-09-30：生成器の切り替え）

Windows 11、Python 3.12.10、CMake 4.2.3で実行した。アーキテクチャは既定（x64）。GoogleTestは `--online` で固定版1.14.0を取得した。3つの生成器で、それぞれ6シナリオすべてPASS、終了コード0、操作・確認は各84件。

| シナリオ | 操作・確認数 | VS2022（秒） | Ninja Multi-Config＋MSVC（秒） | VS2026・.slnx（秒） |
| --- | ---: | ---: | ---: | ---: |
| lifecycle | 27 | 9.55 | 7.64 | 9.61 |
| libraries | 12 | 19.74 | 12.16 | 23.55 |
| external | 10 | 9.25 | 8.44 | 10.73 |
| execution | 10 | 9.76 | 6.42 | 10.06 |
| templates | 13 | 6.75 | 5.76 | 7.91 |
| testing | 12 | 48.17 | 49.62 | 48.50 |
| 合計 | 84 | 103.22 | 90.04 | 110.36 |

出力先（Git除外のローカル生成物）：`.test-work/usage-final-vs2022/`、`.test-work/usage-final-ninja/`、`.test-work/usage-final-vs2026-2/`。

経緯：
- 最初の実行（`.test-work/usage-generators-vs2022/`）では、librariesが失敗した。ランナーが `ae714f1` で廃止された `ProjectType.HEADER_ONLY` を使っていたためで、`INTERFACE_LIBRARY` へ修正した。これは今回の変更より前からの不具合。
- VS2026の1回目（`.test-work/usage-final-vs2026/`）では、templatesが失敗した。Solutionテンプレートのステージングディレクトリのrenameで、WinError 5（アクセス拒否）が断続的に発生した（単独の再実行3回中1回）。新規作成直後のファイルを別プロセスが一時的に開いているためとみられる。Windowsのrenameは既存の宛先を上書きしないため、`storage.rename_no_replace` でアクセス拒否時に最大10秒再試行するよう修正した。修正後、templates単独6回と全シナリオの再実行がすべてPASS。
- ランナーに `--generator`／`--architecture`／`--toolset` を追加した。テンプレートから復元したSolutionにも、同じ生成環境を再指定する。

未実施：Linux/macOS、x86/ARM64での全シナリオ、VS IDEのGUI操作。

# 実行結果（2026-09-19）

Windows / VS2022 / x64、Python 3.12、既存のGoogleTest 1.14.0ローカルZIPを用いて実行。6シナリオすべてPASS、終了コード0。記録した操作・確認は84件、シナリオ所要時間の合計は103.56秒。

| シナリオ | 操作・確認数 | 秒 | 結果 |
| --- | ---: | ---: | --- |
| lifecycle | 27 | 9.94 | PASS |
| libraries | 12 | 20.97 | PASS |
| external | 10 | 9.25 | PASS |
| execution | 10 | 10.16 | PASS |
| templates | 13 | 6.97 | PASS |
| testing | 12 | 46.27 | PASS |

件数はランナーが記録したAPI操作と結果の確認の合計であり、独立した単体テスト数やC++テストケース数ではない。意図的な失敗を正しく検出したケースもPASSに含む。

生成したプロジェクトと詳細ログ：

- ローカル出力ディレクトリ：`.test-work/usage-verified-20260919/`
- 集計：同ディレクトリの`summary.json`
- 各シナリオ配下の`steps.json`にコマンド・終了コード・出力・確認結果を保存。

これらはGitに含めないローカル生成物です。別のチェックアウトではREADMEの手順で再生成してください。

初回のサンドボックス実行では環境診断とテンプレート作成が一時ディレクトリへのアクセス制限で失敗した。承認後、上記の新規出力先で制限外実行を行い全シナリオを再確認した。初回のログは`.test-work/usage-validation-20260919/`に別途残っている。

今回ライブラリ本体は変更していない。既存unittestの再実行、オンラインGoogleTest取得、x86/ARM64、VS IDEのGUI操作は実施していない。再実行には[README](README.md)のコマンドを使う。生成物はGit除外対象であり、別のチェックアウトでは再生成が必要。
