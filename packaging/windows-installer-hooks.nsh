; The main executable used to be screator-desktop.exe. Tauri's normal
; preinstall check only checks the new executable name, so also check the
; previous name before replacing worker files during an in-place upgrade.
; The bundled macro honors installMode=currentUser and asks before closing
; the old app in an interactive installer.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "screator-desktop.exe" "screator"
!macroend
