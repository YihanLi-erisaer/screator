//! Explicit uninstaller mode, before installation, single-instance and worker startup.
use std::{path::{Path, PathBuf}, process::Command};
use objc2_foundation::{NSFileManager, NSString, NSURL};
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogResult};
use crate::macos_install::{Desktop, MacDesktop, InstallLock, validate_app};

const KEEP: &str = "卸载并保留数据";
const DELETE: &str = "卸载并删除数据";
const CANCEL: &str = "取消";

#[derive(Clone, Copy, PartialEq, Debug)]
enum Action { Keep, Delete, Cancel }

trait Uninstaller {
    fn choose(&self, message: &str, confirm_delete: bool) -> Action;
    fn running(&self) -> Result<bool, String>;
    fn clean_data(&self) -> Result<(), String>;
    fn trash(&self, app: &Path) -> Result<(), String>;
}

struct NativeUninstaller {
    app: tauri::AppHandle,
    resources: PathBuf,
}

impl Uninstaller for NativeUninstaller {
    fn choose(&self, message: &str, confirm_delete: bool) -> Action {
        let buttons = if confirm_delete {
            MessageDialogButtons::OkCancelCustom(DELETE.into(), CANCEL.into())
        } else {
            // The first/default action retains models and history.
            MessageDialogButtons::YesNoCancelCustom(KEEP.into(), DELETE.into(), CANCEL.into())
        };
        match self.app.dialog().message(message).title("卸载 screator")
            .buttons(buttons).blocking_show_with_result() {
            MessageDialogResult::Custom(label) if label == KEEP => Action::Keep,
            MessageDialogResult::Custom(label) if label == DELETE => Action::Delete,
            MessageDialogResult::Yes => Action::Keep,
            MessageDialogResult::No => Action::Delete,
            MessageDialogResult::Ok if confirm_delete => Action::Delete,
            _ => Action::Cancel,
        }
    }

    fn running(&self) -> Result<bool, String> {
        MacDesktop(self.app.clone()).other_copy_running()
    }

    fn clean_data(&self) -> Result<(), String> {
        let result = Command::new(self.resources.join("worker/screator-worker"))
            .args(["--uninstall-data", "--confirm"]).output()
            .map_err(|error| format!("无法启动数据清理工具：{error}"))?;
        if !result.status.success() {
            return Err(format!("数据清理未完成，应用尚未移除。部分文件可能已被清理。\n{}",
                String::from_utf8_lossy(&result.stderr).trim()));
        }
        Ok(())
    }

    fn trash(&self, app: &Path) -> Result<(), String> {
        let url = NSURL::fileURLWithPath(&NSString::from_str(&app.to_string_lossy()));
        NSFileManager::defaultManager().trashItemAtURL_resultingItemURL_error(&url, None)
            .map_err(|error| format!("无法将 {} 移到废纸篓：{error}", app.display()))
    }
}

fn uninstall(apps: &[PathBuf], desktop: &impl Uninstaller) -> Result<bool, String> {
    for app in apps { validate_app(app)?; }
    let _install_locks = apps.iter().map(|app| {
        InstallLock::acquire(app.parent().ok_or("应用路径无效。")?)
    }).collect::<Result<Vec<_>, String>>()?;
    if desktop.running()? { return Err("请先退出 screator，再运行卸载工具。".into()); }
    let locations = if apps.is_empty() { "未发现标准位置的应用；可选择清理残留数据。".into() }
        else { apps.iter().map(|app| app.display().to_string()).collect::<Vec<_>>().join("\n") };
    let action = desktop.choose(&format!("将以下应用移到废纸篓：\n{locations}\n\n请选择是否保留本机数据。默认保留模型、历史记录、设置和素材，重装后可继续使用。\n\n删除数据将清理 ~/Library/Application Support/StarDazz/ 下的 screator 和旧版 yt2bili 目录。自定义目录、外部 Ollama 和系统钥匙串不清理。"), false);
    if action == Action::Cancel { return Ok(false); }
    if action == Action::Delete && desktop.choose("永久删除模型、历史记录、设置及默认素材？\n\n删除后重装需要重新下载模型，历史记录需从备份导入。此操作不可撤销。", true) != Action::Delete {
        return Ok(false);
    }
    // The user may have opened the app while the dialog was visible.
    if desktop.running()? { return Err("screator 正在运行，请先退出再卸载。".into()); }
    for app in apps { validate_app(app)?; }
    if action == Action::Delete { desktop.clean_data()?; }
    for app in apps { desktop.trash(app)?; }
    Ok(true)
}

pub fn handle_uninstall_launch(mut context: tauri::Context<tauri::Wry>) -> Option<tauri::Context<tauri::Wry>> {
    if !std::env::args().any(|arg| arg == "--uninstall") { return Some(context); }
    context.config_mut().app.windows.clear();
    tauri::Builder::default().plugin(tauri_plugin_dialog::init()).setup(|app| {
        let handle = app.handle().clone();
        let resources = app.path().resource_dir()?;
        std::thread::spawn(move || {
            let mut candidates = vec![PathBuf::from("/Applications/screator.app")];
            if let Some(home) = std::env::var_os("HOME") {
                candidates.push(PathBuf::from(home).join("Applications/screator.app"));
            }
            let apps = candidates.into_iter().filter(|app| app.symlink_metadata().is_ok()).collect::<Vec<_>>();
            let desktop = NativeUninstaller { app: handle.clone(), resources };
            let message = match uninstall(&apps, &desktop) {
                Ok(false) => { handle.exit(0); return; }
                Ok(true) => "卸载完成。应用已移到废纸篓（如存在），本机数据已按你的选择处理。".into(),
                Err(error) => format!("卸载未完成：{error}"),
            };
            let _ = handle.dialog().message(message).title("卸载 screator")
                .buttons(MessageDialogButtons::Ok).blocking_show_with_result();
            handle.exit(0);
        });
        Ok(())
    }).run(context).expect("无法启动 screator 卸载窗口。");
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    struct Fake {
        choices: RefCell<Vec<Action>>,
        cleaned: Cell<bool>,
        checks: Cell<usize>,
        running_on_check: usize,
        fail_cleanup: bool,
        trashed: RefCell<Vec<PathBuf>>,
    }
    impl Fake {
        fn new(choices: Vec<Action>) -> Self {
            Self { choices: RefCell::new(choices), cleaned: Cell::new(false), checks: Cell::new(0), running_on_check: 0, fail_cleanup: false, trashed: RefCell::new(Vec::new()) }
        }
    }
    impl Uninstaller for Fake {
        fn choose(&self, _: &str, _: bool) -> Action { self.choices.borrow_mut().remove(0) }
        fn running(&self) -> Result<bool, String> {
            self.checks.set(self.checks.get() + 1);
            Ok(self.running_on_check != 0 && self.checks.get() >= self.running_on_check)
        }
        fn clean_data(&self) -> Result<(), String> {
            if self.fail_cleanup { return Err("busy".into()); }
            self.cleaned.set(true); Ok(())
        }
        fn trash(&self, app: &Path) -> Result<(), String> {
            self.trashed.borrow_mut().push(app.to_owned()); Ok(())
        }
    }
    #[test]
    fn keep_and_cancel_never_clean_data() {
        for action in [Action::Keep, Action::Cancel] {
            let fake = Fake::new(vec![action]);
            assert_eq!(uninstall(&[], &fake).unwrap(), action == Action::Keep);
            assert!(!fake.cleaned.get());
        }
    }
    #[test]
    fn deletion_requires_second_confirmation() {
        for confirm in [Action::Delete, Action::Cancel] {
            let fake = Fake::new(vec![Action::Delete, confirm]);
            assert_eq!(uninstall(&[], &fake).unwrap(), confirm == Action::Delete);
            assert_eq!(fake.cleaned.get(), confirm == Action::Delete);
        }
    }
    #[test]
    fn running_app_or_cleanup_failure_stops_uninstall() {
        for check in [1, 2] {
            let mut fake = Fake::new(vec![Action::Delete, Action::Delete]);
            fake.running_on_check = check;
            assert!(uninstall(&[], &fake).is_err());
            assert!(!fake.cleaned.get());
        }
        let mut fake = Fake::new(vec![Action::Delete, Action::Delete]);
        fake.fail_cleanup = true;
        assert!(uninstall(&[], &fake).is_err());
    }

    #[test]
    fn installed_app_is_only_trashed_after_confirmed_success() {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let folder = std::env::temp_dir().join(format!("screator-uninstall-test-{}-{stamp}", std::process::id()));
        let app = folder.join("screator.app");
        std::fs::create_dir_all(app.join("Contents")).unwrap();
        std::fs::write(app.join("Contents/Info.plist"), r#"<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.stardazz.screator</string></dict></plist>"#).unwrap();
        for (choices, success, cleaned) in [
            (vec![Action::Keep], true, false),
            (vec![Action::Cancel], false, false),
            (vec![Action::Delete, Action::Cancel], false, false),
            (vec![Action::Delete, Action::Delete], true, true),
        ] {
            let fake = Fake::new(choices);
            assert_eq!(uninstall(std::slice::from_ref(&app), &fake).unwrap(), success);
            assert_eq!(fake.cleaned.get(), cleaned);
            assert_eq!(fake.trashed.borrow().len(), usize::from(success));
        }
        let mut fake = Fake::new(vec![Action::Delete, Action::Delete]);
        fake.fail_cleanup = true;
        assert!(uninstall(std::slice::from_ref(&app), &fake).is_err());
        assert!(fake.trashed.borrow().is_empty());
        {
            let _installer = InstallLock::acquire(&folder).unwrap();
            let fake = Fake::new(vec![Action::Delete, Action::Delete]);
            assert!(uninstall(std::slice::from_ref(&app), &fake).is_err());
            assert!(!fake.cleaned.get());
            assert!(fake.trashed.borrow().is_empty());
        }
        std::fs::write(app.join("Contents/Info.plist"), "invalid").unwrap();
        assert!(uninstall(std::slice::from_ref(&app), &fake).is_err());
        assert!(fake.trashed.borrow().is_empty());
        std::fs::remove_dir_all(folder).unwrap();
    }
}
