# CppBuild
C++プロジェクトを管理するための実装やCMakeファイル群

Pythonライブラリとして段階的に実装中です。現行設計は
[API設計](deliverables/API_DESIGN.md)、実装範囲と検証結果は
[実装記録](deliverables/IMPLEMENTATION_STATUS.md)、完了条件は
[マイルストーン](deliverables/MILESTONES.md)を参照してください。

テスト: `python -m unittest discover -s tests -v`

実VS2022試験（PowerShell）:

```powershell
$env:CPPBUILD_TEST_VS2022='1'
python -m unittest discover -s tests -v
```

独立Projectの作成・実ビルド・実行例:

```powershell
python -m examples.independent_project C:\work\NewCppBuildDemo
```

現段階の実ビルドにはCMakeとVS2022のC++ツールが必要です。
Solution全体のビルド、リンク依存、GoogleTest等はまだ実装していません。
