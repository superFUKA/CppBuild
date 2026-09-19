# 開発・貢献手順

リポジトリのルートでPython仮想環境を作り、編集可能な形でインストールします。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m unittest discover -s tests -v
```

通常実行は実VS2022試験をskipします。実ビルド試験にはREADMEのC++ツール一式が必要です。

```powershell
$env:CPPBUILD_TEST_VS2022 = '1'
$env:CPPBUILD_TEST_GTEST_ARCHIVE = 'C:/archives/v1.14.0.zip'
.venv/Scripts/python -m unittest discover -s tests -v
```

ZIPはGoogleTest 1.14.0の固定アーカイブです。オンライン取得の確認は新しい出力先を指定して`python -m examples.complete_workflow .test-work/online-demo`で行います。

公開APIからC++ Solutionを作成し、実ビルド・実行まで確認する6シナリオは[利用検証手順](usage_tests/README.md)を参照してください。結果と生成物は`.test-work/`へ保存し、コミットにはランナー・手順・検証結果の要約だけを含めます。

変更時は関連するテストを実行し、実行条件・成功／失敗・skipを記録してください。公開APIや制約を変えた場合は`deliverables/`の対応資料も更新します。生成したプロジェクトや取得したGoogleTestをコミットしないでください。

## 配布物の確認

```powershell
python -m pip install build
python -m build
```

`dist/`にwheelとソース配布物を作成します。wheelは`cppbuild/`のみ、ソース配布物はコード・テスト・例・現行資料を含めます。設計履歴やローカルの生成物は配布対象外です。公開前にライセンスと配布内容を確認してください。

不具合報告にはPython・CMake・VSのバージョン、再現コード、期待結果、工程の出力を含めてください。認証情報や個人用パスは伏せてください。
