# 生成器・コンパイラ切り替えの対応計画

更新日：2026-09-30。状態：ユーザー確認後に実装済み（未コミット）。Windows上のVS2022・VS2026・Ninja（MSVC）で検証済み、Linux/macOSは未検証。実装範囲・検証は [実装記録](IMPLEMENTATION_STATUS.md) を優先する。本資料は計画時点の記録。

## 目的と現在地

ユーザーは、VS2022固有の用途以外をWindows限定にせず、CMakeが扱う生成形式とコンパイラを選び、ビルドまで操作したい。今回の依頼は、この対応内容と次の作業を引き継げるようにまとめること。

現在の製品コードは `ae714f1`。形式切り替えまでコミット済みで、直近の検証は全136件（実VS2022試験20件・skipなし）成功。今回、製品コードの変更・テスト再実行はしていない。Windows依存の詳細と公式資料は [CROSS_PLATFORM_REVIEW.md](CROSS_PLATFORM_REVIEW.md) を参照。

## 方針と提案の区別

ユーザーが示した方向性は、生成器・コンパイラを選択し、通常操作を非Windowsでも提供すること。以下のAPI名・既定値・段階的な対応順序はエージェントの提案であり、個別に承認済みの仕様ではない。

初回はVS2022とNinja Multi-Configを候補とし、共通部分を作った後でNinja・Unix Makefiles・Xcodeなどへ広げる。全生成器を一度に完全対応すると約束しない。CMake上の利用可否、本ライブラリの操作対応、実環境での検証済み範囲を区別する。

## API案

既存の `SolutionBuildSettings` に `cmake: CMakeSettings`、`ProjectBuildSettings` に `cmake: CMakeSettings | Inheritance = INHERIT` を追加する案。

```python
solution.set_build_settings(
    SolutionBuildSettings(
        configuration="Debug",
        parallel=8,
        cmake=CMakeSettings(
            generator="Ninja Multi-Config",
            cxx_compiler="clang++",
        ),
    )
)

solution.update()
solution.build()
solution.clean()
solution.rebuild()
solution.run()
solution.test()
```

この例は未実装。Projectにも既存の同名操作を維持する。

| CMakeSettingsの項目案 | 意味 |
| --- | --- |
| `generator` | CMakeの生成器名。固定Enumに限定せず文字列で受け取る |
| `platform` | VSのx64等、生成器が対応するプラットフォーム指定 |
| `toolset` | VSのv143・ClangCL等の指定 |
| `c_compiler` / `cxx_compiler` | コンパイラ名またはパス |
| `toolchain_file` | CMakeツールチェーンファイル |

各項目の省略時はCMakeの既定・環境に従う案。ただし、この場合の既定生成器と本ライブラリの対応範囲の不一致をどう報告するか、実装前に決める。既定をNinjaへ固定することは合意していない。

現在の `architecture` は生成器固有の `platform` とツールチェーンの対象CPU設定へ整理する案。既存引数の互換性・廃止手順を決める。`toolchain_file` と直接のコンパイラ指定は初期案では排他的。VSでは `toolset` を用いる。非対応の組み合わせを黙って無視しない。

`ToolSettings` のcmake/ctest実行パス・環境変数は維持し、新設定と重複させない。設定は管理JSONへ保存しない。CMake自身のキャッシュ保存と、この非保存方針は区別する。

### 操作と戻り値

- `update()`：選択した生成器で生成する。全体／個別ともVSファイルの存在を必須にしない。
- `build()`：必要な生成後、依存順にビルド。基本は `cmake --build` を利用する。
- `clean()`：既存の所有ツリーだけを対象にする。事前configure禁止、共有依存保護を維持する。
- `rebuild()`：既存の対象選択・保護規則を保ちながらcleanとbuildを行う。
- `run()` / `test()`：既存の実行・テスト選択や順序を維持し、成果物と共有ライブラリ探索を環境に合わせる。クロスコンパイルした成果物の実行は初回対応に含めない。
- 生成結果は `success`、`generator`、`build_directory`、`files` を共通情報とする案。既存 `solution_file` / `project_file` / `filters_file` は該当時のみ設定する。全体は現在OperationReport、個別はUpdateReportなので、戻り値の統一または拡張方法も具体化する。
- `Environment.generators()` を追加する案。CMakeから得る生成器名・platform/toolset対応情報と、本ライブラリが対応する操作を返す。`check_environment()` は選択した環境を検査する。

## 2026-09-30 具体案（ユーザー確認待ち）

上記のAPI案を現行コードと照合した具体案。確認後に現行設計資料へ反映する。「確認事項」は利用者の操作や互換性が変わるため、ユーザーの判断を待つ。

### 設定

```python
@dataclass
class CMakeSettings:            # 非保存。Solution既定、ProjectはINHERIT（オブジェクト単位で継承）
    generator: str | None = None    # None: Windows=Visual Studio 17 2022、その他=Ninja Multi-Config
    toolset: str | None = None      # VSの -T のみ
    c_compiler: str | None = None   # VS以外。名前または絶対パス
    cxx_compiler: str | None = None
    toolchain_file: str | None = None  # 絶対パス。コンパイラ指定とは排他
```

- `SolutionBuildSettings.cmake = CMakeSettings()` と `ProjectBuildSettings.cmake = INHERIT` を追加する。項目ごとではなくオブジェクト単位で継承し、生成器とコンパイラの組み合わせが混ざらないようにする。
- `generator=None` はCMakeの既定を使わず、ホスト別の固定値に解決する。VS 2026導入環境では、CMakeの既定がVS 2026になるため。Windowsの既存利用は無指定のまま現行と同じ結果になる。
- 初回に受け付ける生成器は `Visual Studio 17 2022` と `Ninja Multi-Config` だけ。それ以外はCMakeが対応していても `SettingsError`（未対応）とし、黙って実行しない。
- `platform` 項目は新設せず、既存の `architecture` をVSの `-A` として維持する。既定値を `"x64"` から `None` に変える。VSでは `None` をx64と扱い、VS以外で明示された場合はエラーとする。VS以外の対象CPUは、コンパイラ環境またはツールチェーンで決める。
- `ToolSettings` はcmake/ctestのパスと子プロセスの環境変数の役割を維持する。WindowsでNinjaとMSVCを使う場合は、開発者コマンドプロンプトの環境で起動するか、`ToolSettings.environment` で環境を渡す。vcvarsの自動実行は初回の範囲に含めない。

### 生成環境とキャッシュ

- 生成環境は、生成器・architecture・toolset・解決済みのコンパイラの絶対パス・ツールチェーンの絶対パスで識別する。依存でつながる全Projectと、全体操作・外部Solutionの上書き設定では、生成環境とconfigurationの一致を要求する（現行の一致判定を拡張）。
- VS2022の既定は既存の `vs2022-<arch>-<type>` を維持し、既存キャッシュを再利用する。その他は `ninja-mc-<環境ハッシュ12桁>-<type>` とし、所有情報に生成環境を記録する。不一致のツリーは使わない。
- ツールチェーンファイルの内容の変更と、コンパイラ無指定時の環境変数の変化は検出しない。この制約は資料に明記する。

### 成果物・リンク・実行

- 生成するCMakeListsで `file(GENERATE)` を使い、構成ごとの `cppbuild-outputs-<Config>.json`（TARGET_FILE／TARGET_LINKER_FILE）を出力する。両生成器とも、これを依存先のIMPORTED設定と実行ファイルの特定に使う。拡張子で判定しない。
- `ImportedLibrary` のインポートライブラリの必須判定は、生成するCMake側で行う。インポートライブラリ用の拡張子がある対象（Windows）だけで必須とする。
- 実行時、Windowsでは従来どおり.dllの場所をPATHに追加する。Linux/macOSでは、CMakeがビルドツリーに設定するRPATHを使う。

### 操作

- `Project.update/build/clean/rebuild/run/test` は、生成器ごとの処理で共通化する。VS以外の `UpdateReport` では、sln/vcxproj/filtersを `None` にする。`generator` 項目を末尾に追加する（既定値あり、互換）。
- `Solution.update/build/...` について：
  - VSは現行の.sln統合とMSBuildを維持する。
  - VS以外はPython側で、選択Projectの依存閉包を依存順に1回ずつ `cmake --build` する。全体用のIDEファイルは作らず、同じツリーを個別操作と共用する。
  - 未参照Projectの表示用列挙（solution_folders）はVSだけに適用する。
- VS以外のcleanは `cmake --build <所有ツリー> --config C --target clean` を使う。事前の構成はせず、所有情報・CMakeCache・`build-<C>.ninja` の存在を前提とする。
  - 削除範囲は、そのProjectのツリーの該当構成。ツリー内部のGoogleTestとCMakeSourceの成果物を含む。他の管理Projectは別ツリー（IMPORTED）なので削除されない。
  - VSの `/t:Clean` と違い、ツリー内部の依存物も削除する。.pdb等の一部は残る。
- `Environment.check` はVSでは現行の `vs2022` 項目を維持し、その他では `compiler` 項目として選択環境で試験ビルドする。`Environment.generators()` はCMakeの `-E capabilities` の一覧に、本ライブラリの対応可否を付けて返す。検証済みかどうかは資料の対応表で管理する。

### 初回の実装範囲案

1. CMakeSettings・継承・検証、architectureの既定変更、生成環境の識別とキャッシュ・所有情報。
2. 成果物の役割出力と、それを使ったリンク・実行・テスト（VSも移行して回帰確認）。
3. Ninja Multi-Configでの個別・全体の update/build/clean/rebuild/run/test、環境診断と `Environment.generators()`。
4. 試験：生成器に依存しない単体試験、Windows＋MSVC＋Ninja Multi-Configの実試験、既存VS2022の全試験の回帰確認。

後回しにする範囲：
- 非Windowsでの移動・テンプレート展開。案は、ディレクトリは宛先を排他作成してからrename、ファイルはlinkしてからunlink。
- 単一構成の生成器（Ninja/Makefiles）、Xcode、VS 2026、ClangCL等のtoolsetの実試験、クロスコンパイル。
- Linux/macOSでの実検証。この環境では実行できない。

### ユーザー回答（2026-09-30）と反映案

- 1：OSごとの固定値で合意。
- 2：本ライブラリがVSを前提にしている箇所はすべて修正対象。これにより、`architecture` をVS専用にする案と、非Windowsの移動を後回しにする案は取り下げる。代わりの案（確認待ち）：
  - `architecture=None` を「ホスト／コンパイラの既定」とする。明示値（x64/Win32/ARM64）はどの生成器でも有効にする。VSでは `-A` へ対応させる。
  - WindowsでVS以外の生成器とMSVCを使う場合は、vswhereとvcvarsallで指定アーキテクチャのMSVC環境を自動で用意する。開発者コマンドプロンプトを前提にしない。
  - GCC/Clangでは構成後に、CMakeが検出した対象アーキテクチャ（`CMAKE_CXX_COMPILER_ARCHITECTURE_ID`、ポインターサイズ）を照合する。不一致なら失敗にする。-m32等のフラグ注入やクロスコンパイルは `toolchain_file` に任せる。
  - 非Windowsの移動・テンプレート展開（排他作成＋rename、link＋unlink）を初回の範囲に含める。
- 3：ユーザーの質問「Ninjaのほうが直感的か」への回答案：
  - CMake標準の `--target clean` は「そのビルドツリーの該当構成の成果物をすべて消す」。VSの生成器でも同じ意味になる。
  - 現行VSの `/t:Clean`（対象ターゲットだけ）はMSBuildの事情による。ツリー内部のGoogleTest等を残すのでビルドは速いが、「Projectのclean」としては例外的な挙動。
  - 他の管理ProjectはツリーがProjectごとに分かれているので、どちらの方式でも保護される。
  - 提案：両生成器を「所有ツリーの該当構成を全削除（CMake標準のclean）」に統一する（確認待ち。VS側の挙動変更は回帰試験で確認）。
- 4：GitHub Actionsで検証する。windows-latest（VS2022＋Ninja Multi-Config）、ubuntu-latest（GCC、Clang）、macos-latest（AppleClang）のマトリクス案。
  - 実行にはpushが必要。現在、mainはoriginより2コミット先行している。
  - Actionsの成功を各OSの検証済みとして記録する。ローカルで未実行の環境は区別して記録する。

### ユーザー回答2（2026-09-30）

- 3：Ninjaの挙動に合わせる（合意）。全生成器で、cleanは「所有ツリーの該当構成を `--target clean` で削除」とする。VSの `/t:Clean` 方式は廃止する。
- 4：GitHub Actionsは当面行わない。手元の環境で、コンパイラとソリューションファイルの形式を切り替えて検証する。Linux/macOS向けの処理は実装するが、「未検証」と明記する。
- 手元で検証できる組み合わせ（2026-09-30確認）：
  - 生成器：`Visual Studio 17 2022`（.sln）、`Visual Studio 18 2026`（.slnx、既定ツールセットv145）、`Ninja Multi-Config`（VS付属のninja）。
  - コンパイラ：MSVC 14.44（v143）、MSVC 14.51（v145）。
  - 対象：x64／x86／ARM64のビルド。ARM64の実行はホストがx64のため不可。
  - clang-cl・GCC・MinGWは未導入。
- これに伴い、VS 2026（.slnx）を初回の範囲に含める。全体.slnの統合、MSBuildのターゲット指定、ファイル名の.sln前提を生成器別にする。
- 2：architectureの詳細案を以下に示す（確認待ち）。

### architectureの詳細案（確認待ち）

値は `None`（既定）、`"x64"`、`"Win32"`、`"ARM64"`。既存の値の名前を維持する。`Win32` は32ビットx86を意味する。

| 生成器・コンパイラ | `None` | 明示値 |
| --- | --- | --- |
| VS 2022/2026 | ホストのアーキテクチャに解決して `-A` へ渡す（x64ホストでは現行と同じ `vs2022-x64-...` を使う） | `-A` へ渡す |
| Ninja＋MSVC（Windows） | vswhereで見つけたVSのvcvarsallを、ホスト→ホストの対象で実行し、その環境を子プロセスに渡す | vcvarsallの対象（x64／x64_x86／x64_arm64）で環境を作る |
| Ninja＋GCC/Clang（Linux） | コンパイラの既定 | 構成後に検出値と照合し、不一致なら失敗にする。-m32等のフラグ注入はせず、`toolchain_file` を案内する |
| Ninja＋AppleClang（macOS） | コンパイラの既定 | `CMAKE_OSX_ARCHITECTURES` に対応する値（x86_64/arm64）を渡す。Win32はエラー |

- MSVCの選択：Windowsで生成器がVS以外かつコンパイラ無指定の場合は、最新のVSのMSVCを使う。`toolset` にMSVCのバージョン（例 `"14.44"`）を指定すると、vcvarsallの `-vcvars_ver` に渡す。VSの生成器では、従来どおり `-T`（例 `v143`）の意味とする。
- `ToolSettings.environment` に開発者コマンドプロンプトの環境が既にある場合（`VSCMD_ARG_TGT_ARCH` あり）は、vcvarsallを実行せずにそれを使う。対象が明示値と異なればエラー。
- 照合には、CMakeが記録する `CMAKE_CXX_COMPILER_ARCHITECTURE_ID`、`CMAKE_SIZEOF_VOID_P`、`CMAKE_SYSTEM_PROCESSOR` を使う。対応付け：x64 = x64/AMD64/x86_64、Win32 = X86/i?86かつポインター4バイト、ARM64 = ARM64/aarch64/arm64。
- キャッシュの識別には、解決後のアーキテクチャを含める。依存でつながるProject間では、解決後の値の一致を要求する。
- 実行できない対象（x64ホストでのARM64ビルド等）では、run/testを実行前に明確なエラーにする。ビルドは可能。
- vcvarsallで得た環境は、同じプロセス内で（VS・対象・バージョン）ごとにキャッシュする。

### 確認事項（初回提示時）

1. `generator=None` の解決方法：ホスト別の固定値（推奨）か、明示指定を必須にするか。
2. `architecture` の既定を `None` に変え、VS専用の意味にしてよいか。
3. Ninjaのcleanで、ツリー内部の依存物（GoogleTest等）も削除される違いを許容するか。
4. Linux/macOSの検証手段（GitHub Actions等）と、非Windowsの移動を初回に含めるか。

## 実装前に具体化する点

1. 新設定の既定値、相対パスの基準、継承単位、旧architectureとの互換性。
2. 全体ビルド・外部Solutionを含む設定整合条件。初期案では依存でつながるProjectの生成環境を揃える。異なるコンパイラ間のABI互換性を自動保証しない。
3. 生成器・解決済みコンパイラ・ツールチェーン・対象環境・Project形式からビルド領域を識別する規則。単一構成の生成器ではconfigurationも分離する。環境の変更やツールチェーン内容の変更で古いキャッシュを誤使用しない。
4. CMake上は利用可能でも本ライブラリで未対応の操作を明示的に報告する方法。環境検査と実生成で判明する条件を分ける。
5. 各Projectの独立生成を保った全体IDE表示。全体ビルド対応と、全Projectを一つのIDEファイルに統合する対応は分ける。Xcodeの全体統合をVSと同等に提供できるとは未確認。

これらは既存方針内で具体化し、公開APIの互換性や利用者の操作が変わる判断を整理して提示する。既に示された非Windows対応の希望を繰り返し確認する必要はない。

## 次の実装順序

1. **設定・レポートの設計**：上記判断を整理し、API_DESIGN・SETTINGS_DESIGN・BUILD_DESIGNと要件へ反映する。
2. **生成処理の分離**：VS固定処理を分離し、生成器の選択、環境検査、キャッシュ・所有情報を実装する。
3. **個別操作**：Ninja Multi-Configのupdate/build、CMakeの情報を使った成果物識別・リンク、run/testを実装する。拡張子の追加だけで対応しない。
4. **全体操作とclean**：Python側で依存順実行・共有依存の重複排除・選択ビルドを実装する。生成器別cleanの削除範囲を確認する。未参照ProjectのIDE表示とビルド対象を分ける。
5. **非Windowsの管理操作**：ファイル・Project移動、テンプレート展開の宛先競合時の上書き防止と失敗復元、パス・権限の扱いを実装する。OS判定だけを削除しない。
6. **実環境検証と資料更新**：Windows/MSVC、Linux/GCCまたはClang、macOS/AppleClangで試験し、対応表と制約を更新する。その後、他の生成器へ広げる。

個別／全体の成果物共用は同じ生成環境内で維持し、異なる生成器のキャッシュは共用しない。必須の共通add_subdirectoryツリーへ戻さない。GUID優先・相対パス保存・形式切り替えの既存仕様も維持する。

## 完了判定と注意点

- 各対応環境で、個別／全体のupdate/build/clean/rebuild/run/testを実ビルドで検証する。
- 静的・共有・インターフェース、Debug/Release、外部SolutionとGUID優先、外部CMake・ImportedLibrary、共有ライブラリを使う実行を含める。
- cleanの未生成時成功、不完全ツリーの拒否、未選択Projectの依存保護、構成別の削除範囲を確認する。汎用 `--target clean` への単純置換では完了としない。
- 移動・テンプレートの宛先競合、失敗復元、移設後の再生成を確認する。
- VSの全体.sln表示と既存操作を回帰確認する。他生成器のIDE統合は個別に対応可否を記録する。
- Windows上のNinja成功をLinux/macOS検証済みとしない。未検証環境は明記する。
- 外部バイナリや利用者のC++ソース自体のOS依存は、生成器切り替えでは解消されない。

今回の資料更新は未コミット。既存のアクセス問題がある未追跡 `relocation-check-l3g0bdd1/` は対象外。次の担当は開始時のGit状態を確認し、このディレクトリを一括追加・削除しない。
