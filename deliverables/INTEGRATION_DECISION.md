# 全体統合：実測結果と判断が必要な事項

2026-09-17。M3aの検証結果。**製品方式としては未採用**。
根拠となる設計の状態はBUILD_DESIGN.md「最新の合意」とAPI_DESIGN.mdのジャンル5・7。

## 検証した構成

`tests/test_integration_candidate.py`は一時ディレクトリに以下を作り、終了時に除去する。
製品のSolution.update/buildやリンクAPIは実装していない。

- Math、App、Toolにそれぞれ独立したCMake入口・VS2022ビルドツリー。
- Mathはstatic library、AppはMathの生成物をIMPORTEDライブラリとしてリンク、Toolは独立。
- 全体CMakeはinclude_external_msprojectで既存.vcxprojを登録し、実際のProjectGuidを指定。
- 全体のadd_dependencies(App Math)により.slnへ依存を記録。
- Debug/Release、x64、同一VS2022環境。

## 実測

1. 全体の`cmake --build ... --target App`は外部App.vcxprojを直接ビルドし、全体.slnだけに記録されたMath依存をビルドしなかった。Math.lib未生成の初回にリンク失敗。単に登録するだけでは要件を満たさない。
2. MSBuildへ全体.slnと`/t:App`を渡すとMath→Appの順で成功し、Toolはビルドされない。
3. 全体CMakeに、そのMSBuild呼び出しを行うcustom targetを生成すれば、公開のビルド入口を`cmake --build ... --target cppbuild_App`に保ったまま同じ依存ビルドを実現できた。Pythonから個別Project.buildを繰り返す方式ではない。
4. Appへソースを追加して個別CMakeだけ再生成した後、全体.slnを書き換えずに追加ソースを含めた全体ビルド・実行が成功した。Tool.vcxprojの内容も不変。
5. 個別App Debug clean後もMath Debug、Tool Debug、App Releaseの成果物内容は保持された。
6. 全体ビルドのApp Debug成果物は個別App Debugの成果物と同一。個別cleanでこの共有成果物が消え、全体ビルドで再作成された。

全体統合は[include_external_msproject](https://cmake.org/cmake/help/latest/command/include_external_msproject.html)を使った。上記は公式機能の説明だけでなく、CMake 4.2.3・VS2022 17.14で再現した挙動。

## 必須判断：全体／個別の成果物を共用するか

**A：個別生成.vcxprojと成果物を全体でも共用（推奨案）**

- 上記の統合・CMake経由のMSBuild制御を製品化する。
- 同一Project・種類・構成の成果物は一つ。個別cleanは、その同じ成果物を全体からも使えなくする。次の全体buildで再生成する。
- 他Project・他構成の所有成果物は保護する。共有依存の所有範囲・選択cleanは追加実装が必要。
- ビルド重複を減らし、個別更新を全体が参照する構成に合う。
- 既存文書の「別生成範囲の成果物を消さない」との関係を、同一成果物を共有する場合として明文化する必要がある。確認済みとして独断で扱わない。

**B：全体／個別の成果物を分離**

- 全体用に別の.vcxproj・ビルドツリーを作るなど、別方式が必要。
- 個別clean後も全体の成果物を保持できるが、同じソースの重複ビルド・出力・状態管理が増える。
- 個別更新は引き続き独立CMakeとする。撤回済みの共通ツリーを個別updateの前提に戻さない。
- 全体用.vcxprojを別生成する案も既存資料では未合意。Aの検証結果をBの検証済み根拠にはしない。

この選択は全体の依存伝達・成果物の識別・cleanの意味を左右し、M3bの実装継続に必須。
それ以外の追加機能の確認を今回の停止理由にはしない。

## 未検証・保留

- 複数architecture/SDK/CRT、異なる構成対応、DLL・HEADER_ONLY・TESTの統合。
- GUID・参照先・種類・所属変更時の全体.sln再生成条件。通常のソース追加と同一とは扱わない。
- 個別App.build時の未生成依存の準備、全体の任意選択集合、依存循環診断。
- 全体cleanの選択範囲・共有依存保護、同時操作のロック、IDE GUIでの表示。

M3aの技術検証をM3bの完成や方式への合意と混同しない。
