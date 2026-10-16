; The main executable used to be screator-desktop.exe. Tauri's normal
; preinstall check only checks the new executable name, so also check the
; previous name before replacing worker files during an in-place upgrade.
; The bundled macro honors installMode=currentUser and asks before closing
; the old app in an interactive installer.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "screator-desktop.exe" "screator"
!macroend

; Tauri's existing, unchecked "Delete application data" checkbox normally
; removes only ${BUNDLEID}. Our Python worker owns separate StarDazz profiles.
; Keep silent/passive/update uninstalls non-destructive.
!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode <> 1
  ${AndIf} $PassiveMode <> 1
  ${AndIfNot} ${Silent}
  ${AndIf} $DeleteAppDataCheckboxState = 1
    MessageBox MB_YESNO|MB_ICONEXCLAMATION|MB_DEFBUTTON2 "$(screatorDeleteDataConfirm)" IDYES screator_delete_data
    ; Cancelling this confirmation cancels the whole uninstall.
    SetErrorLevel 1
    Quit
    screator_delete_data:
    !insertmacro CheckIfAppIsRunning "screator.exe" "screator"
    !insertmacro CheckIfAppIsRunning "screator-desktop.exe" "screator"
    nsExec::ExecToStack '"$INSTDIR\worker\screator-worker.exe" --uninstall-data --confirm'
    Pop $0
    Pop $1
    ${If} $0 != 0
      MessageBox MB_OK|MB_ICONSTOP "$(screatorDeleteDataFailed)"
      ; Leave the application and uninstaller available for a retry.
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${Else}
    ; The stock Tauri cleanup must also preserve WebView data in these modes.
    StrCpy $DeleteAppDataCheckboxState 0
  ${EndIf}
!macroend

LangString screatorDeleteDataConfirm 2052 "同时永久删除本机的模型、历史记录、设置及默认素材？$\r$\n$\r$\n包括 StarDazz 下的 screator 和旧版 yt2bili 数据目录。自定义目录、外部 Ollama 和系统凭据库不清理。$\r$\n$\r$\n删除后重装需要重新下载模型，历史记录需从备份导入。选择“否”取消卸载。"
LangString screatorDeleteDataConfirm 1028 "同時永久刪除本機的模型、歷史記錄、設定及預設素材？$\r$\n$\r$\n包括 StarDazz 下的 screator 和舊版 yt2bili 資料目錄。自訂目錄、外部 Ollama 和系統憑證庫不清理。$\r$\n$\r$\n刪除後重裝需要重新下載模型，歷史記錄需從備份匯入。選擇「否」取消解除安裝。"
LangString screatorDeleteDataConfirm 1033 "Permanently delete local models, history, settings and default assets too?$\r$\n$\r$\nBoth StarDazz/screator and legacy StarDazz/yt2bili profiles will be removed. Custom folders, external Ollama and the system credential store are retained.$\r$\n$\r$\nReinstallation will require downloading models and importing a history backup. Choose No to cancel uninstallation."
LangString screatorDeleteDataFailed 2052 "数据清理未完成，卸载已停止。请退出 screator 和命令行任务，检查目录权限后重试；部分文件可能已被清理。"
LangString screatorDeleteDataFailed 1028 "資料清理未完成，解除安裝已停止。請退出 screator 和命令列任務，檢查目錄權限後重試；部分檔案可能已被清理。"
LangString screatorDeleteDataFailed 1033 "Data cleanup failed; uninstallation stopped. Close screator and CLI tasks, check folder permissions and retry. Some files may already have been removed."
