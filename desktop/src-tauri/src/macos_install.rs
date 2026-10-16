//! Install a disk-image copy before starting the main window or Python worker.
use objc2_app_kit::NSRunningApplication;
use objc2_foundation::NSString;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogResult};
use std::{
    fs::{self, File, OpenOptions},
    hash::{Hash, Hasher},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::{Path, PathBuf},
    process::{Command, Output},
    time::{SystemTime, UNIX_EPOCH},
};

const BUNDLE_ID: &str = "com.stardazz.screator";
const APP_NAME: &str = "screator.app";
const CANCEL: &str = "取消";
const OPEN: &str = "打开已安装版本";
const INSTALL: &str = "安装并打开";
const REPLACE: &str = "替换并打开";

pub(crate) trait Desktop {
    fn choose(&self, message: &str, buttons: &[&str], default: &str) -> Result<String, String>;
    fn other_copy_running(&self) -> Result<bool, String>;
    fn open(&self, app: &Path) -> Result<(), String>;
}

pub(crate) struct MacDesktop(pub(crate) tauri::AppHandle);

fn output(command: &mut Command) -> Result<Output, String> {
    let result = command.output().map_err(|error| error.to_string())?;
    if !result.status.success() {
        return Err(String::from_utf8_lossy(&result.stderr).trim().to_owned());
    }
    Ok(result)
}

impl Desktop for MacDesktop {
    fn choose(&self, message: &str, buttons: &[&str], default: &str) -> Result<String, String> {
        let buttons = if buttons.len() == 3 {
            MessageDialogButtons::YesNoCancelCustom(default.into(), OPEN.into(), CANCEL.into())
        } else {
            MessageDialogButtons::OkCancelCustom(default.into(), CANCEL.into())
        };
        // Called on an installer thread while the native event loop keeps running.
        let result = self.0.dialog().message(message).title("screator 安装")
            .buttons(buttons).blocking_show_with_result();
        Ok(match result {
            MessageDialogResult::Custom(label) => label,
            MessageDialogResult::Ok | MessageDialogResult::Yes => default.into(),
            MessageDialogResult::No => OPEN.into(),
            MessageDialogResult::Cancel => CANCEL.into(),
        })
    }

    fn other_copy_running(&self) -> Result<bool, String> {
        // Native AppKit query; no Apple Events / Accessibility permission needed.
        let apps = NSRunningApplication::runningApplicationsWithBundleIdentifier(&NSString::from_str(BUNDLE_ID));
        Ok(apps.iter().any(|app| app.processIdentifier() != std::process::id() as libc::pid_t))
    }

    fn open(&self, app: &Path) -> Result<(), String> {
        // Force a process from this path; otherwise Launch Services may just
        // activate the installer, which has the same bundle ID as the real app.
        // The installed app's single-instance plugin still focuses an older copy.
        output(Command::new("/usr/bin/open").args(["-n", "-a"]).arg(app)).map(|_| ())
    }
}

fn disk_image_app(executable: &Path) -> Option<PathBuf> {
    let app = executable.parent()?.parent()?.parent()?;
    if app.file_name()? != APP_NAME || executable.parent()?.file_name()? != "MacOS"
        || executable.parent()?.parent()?.file_name()? != "Contents" {
        return None;
    }
    // Quarantined DMG apps can run from Gatekeeper's AppTranslocation volume.
    if app.starts_with("/Volumes") || app.components().any(|part| part.as_os_str() == "AppTranslocation") {
        Some(app.to_owned())
    } else {
        None
    }
}

fn plist_value(app: &Path, key: &str) -> Result<String, String> {
    let result = output(Command::new("/usr/bin/plutil").args(["-extract", key, "raw", "-o", "-"])
        .arg(app.join("Contents/Info.plist")))?;
    Ok(String::from_utf8_lossy(&result.stdout).trim().to_owned())
}

pub(crate) fn validate_app(app: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(app).map_err(|error| error.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() || plist_value(app, "CFBundleIdentifier")? != BUNDLE_ID {
        return Err(format!("{} 不是可替换的 screator 应用，请检查安装位置。", app.display()));
    }
    Ok(())
}

fn installed_destination(system: &Path, user: Option<&Path>) -> PathBuf {
    let system_app = system.join(APP_NAME);
    if system_app.symlink_metadata().is_ok() { return system_app; }
    if let Some(user) = user {
        let user_app = user.join(APP_NAME);
        if user_app.symlink_metadata().is_ok() { return user_app; }
    }
    system_app
}

fn version(app: &Path) -> String {
    plist_value(app, "CFBundleShortVersionString").unwrap_or_else(|_| "未知版本".into())
}

// Serialize installers, including two copies launched from different DMGs.
// Keep the inode in place so a waiting process cannot acquire an obsolete lock.
pub(crate) struct InstallLock(File);
impl InstallLock {
    pub(crate) fn acquire(parent: &Path) -> Result<Self, String> {
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        parent.hash(&mut hash);
        let name = format!("screator-install-{}-{:x}.lock", unsafe { libc::geteuid() }, hash.finish());
        let file = OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .custom_flags(libc::O_NOFOLLOW).mode(0o600).open(std::env::temp_dir().join(name))
            .map_err(|error| format!("无法创建安装锁：{error}"))?;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("另一个 screator 安装程序正在运行，请稍后重试。".into());
        }
        Ok(Self(file))
    }
}
impl Drop for InstallLock {
    fn drop(&mut self) { unsafe { libc::flock(self.0.as_raw_fd(), libc::LOCK_UN); } }
}

fn install(source: &Path, destination: &Path, desktop: &impl Desktop) -> Result<(), String> {
    validate_app(source)?;
    let parent = destination.parent().ok_or("安装位置无效。")?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let _lock = InstallLock::acquire(parent)?;
    if destination.symlink_metadata().is_ok() { validate_app(destination)?; }
    if desktop.other_copy_running()? { return Err("请先退出正在运行的 screator，再重新打开安装包进行替换。".into()); }

    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    let staging = parent.join(format!(".screator-install-{}-{stamp}", std::process::id()));
    fs::create_dir(&staging).map_err(|error| format!("无法写入 {}：{error}。请检查文件夹权限，或手动拖入 Applications 安装。", parent.display()))?;
    let staged_app = staging.join(APP_NAME);
    let backup = staging.join("previous.app");
    let result = (|| {
        output(Command::new("/usr/bin/ditto").arg(source).arg(&staged_app))?;
        output(Command::new("/usr/bin/codesign").args(["--verify", "--deep", "--strict"]).arg(&staged_app))?;
        // Copying the large worker can take time; check again before touching the old app.
        if desktop.other_copy_running()? { return Err("screator 正在运行，请退出后重新安装。".into()); }
        if destination.symlink_metadata().is_ok() {
            validate_app(destination)?;
            fs::rename(destination, &backup).map_err(|error| error.to_string())?;
        }
        if let Err(error) = fs::rename(&staged_app, destination) {
            if backup.exists() {
                fs::rename(&backup, destination).map_err(|restore| format!("安装失败：{error}；旧版本保存在 {}，自动恢复失败：{restore}", backup.display()))?;
            }
            return Err(format!("安装失败，旧版本已保留：{error}"));
        }
        // Keep the previous copy until Launch Services accepts the new launch.
        if let Err(error) = desktop.open(destination) {
            if backup.exists() {
                fs::rename(destination, &staged_app).map_err(|restore| format!("启动失败：{error}；旧版本保存在 {}：{restore}", backup.display()))?;
                fs::rename(&backup, destination).map_err(|restore| format!("启动失败：{error}；旧版本保存在 {}：{restore}", backup.display()))?;
            }
            return Err(format!("无法打开应用：{error}。请从 Applications 打开或重新安装。"));
        }
        if backup.exists() { let _ = fs::remove_dir_all(&backup); }
        Ok(())
    })();
    // Never remove a backup that could not be restored or cleaned up.
    if !backup.exists() { let _ = fs::remove_dir_all(&staging); }
    result
}

pub(crate) fn prompt_install(source: &Path, destination: &Path, desktop: &impl Desktop) -> Result<(), String> {
    validate_app(source)?;
    let exists = destination.symlink_metadata().is_ok();
    if exists { validate_app(destination)?; }
    let (message, buttons, default) = if exists {
        (format!("已检测到本机安装了 screator。\n\n已安装版本：{}\n安装包版本：{}\n安装位置：{}\n\n替换前请先退出 screator。账号、设置和任务数据会保留。", version(destination), version(source), destination.display()),
         vec![CANCEL, OPEN, REPLACE], REPLACE)
    } else {
        (format!("将 screator {} 安装到：\n{}\n\n安装完成后自动打开应用。", version(source), destination.display()),
         vec![CANCEL, INSTALL], INSTALL)
    };
    match desktop.choose(&message, &buttons, default)?.as_str() {
        CANCEL => Ok(()),
        OPEN if exists => desktop.open(destination),
        action if action == default => install(source, destination, desktop),
        _ => Err("未识别安装操作，请重新打开安装包。".into()),
    }
}

/// Return the context for normal launches. Never start a worker from the
/// read-only image after an installer cancel/error.
pub fn handle_disk_image_launch(mut context: tauri::Context<tauri::Wry>) -> Option<tauri::Context<tauri::Wry>> {
    let Some(source) = std::env::current_exe().ok().as_deref().and_then(disk_image_app) else { return Some(context); };
    let user = std::env::var_os("HOME").map(|home| PathBuf::from(home).join("Applications"));
    let destination = installed_destination(Path::new("/Applications"), user.as_deref());
    context.config_mut().app.windows.clear();
    tauri::Builder::default().plugin(tauri_plugin_dialog::init()).setup(move |app| {
        let handle = app.handle().clone();
        std::thread::spawn(move || {
            let desktop = MacDesktop(handle.clone());
            if let Err(error) = prompt_install(&source, &destination, &desktop) {
                eprintln!("screator installation: {error}");
                let _ = desktop.choose(&error, &[CANCEL, "好"], "好");
            }
            handle.exit(0);
        });
        Ok(())
    }).run(context).expect("无法启动 screator 安装窗口。");
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::{Cell, RefCell}, os::unix::fs::symlink};

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            let root = std::env::temp_dir().join(format!("screator-install-test-{}-{stamp}", std::process::id()));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }
        fn app(&self, folder: &str, version: &str) -> PathBuf {
            let app = self.0.join(folder).join(APP_NAME);
            fs::create_dir_all(app.join("Contents/MacOS")).unwrap();
            fs::copy("/usr/bin/true", app.join("Contents/MacOS/screator")).unwrap();
            fs::write(app.join("Contents/Info.plist"), format!(r#"<?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
                <plist version="1.0"><dict><key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
                <key>CFBundleExecutable</key><string>screator</string><key>CFBundlePackageType</key><string>APPL</string>
                <key>CFBundleShortVersionString</key><string>{version}</string></dict></plist>"#)).unwrap();
            output(Command::new("/usr/bin/codesign").args(["--force", "--sign", "-"]).arg(&app)).unwrap();
            app
        }
    }
    impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

    #[derive(Default)]
    struct FakeDesktop {
        action: String,
        running_on_check: usize,
        checks: Cell<usize>,
        fail_open: bool,
        messages: RefCell<Vec<String>>,
        opened: RefCell<Vec<PathBuf>>,
    }
    impl FakeDesktop {
        fn choosing(action: &str) -> Self { Self { action: action.into(), ..Self::default() } }
    }
    impl Desktop for FakeDesktop {
        fn choose(&self, message: &str, _: &[&str], _: &str) -> Result<String, String> {
            self.messages.borrow_mut().push(message.into());
            Ok(self.action.clone())
        }
        fn other_copy_running(&self) -> Result<bool, String> {
            self.checks.set(self.checks.get() + 1);
            Ok(self.running_on_check != 0 && self.checks.get() >= self.running_on_check)
        }
        fn open(&self, app: &Path) -> Result<(), String> {
            self.opened.borrow_mut().push(app.to_owned());
            if self.fail_open { Err("test launch failure".into()) } else { Ok(()) }
        }
    }

    #[test]
    fn detects_dmg_and_translocation_but_skips_installed_and_dev() {
        for path in ["/Volumes/screator 1/screator.app/Contents/MacOS/screator", "/private/var/folders/a/AppTranslocation/uuid/d/screator.app/Contents/MacOS/screator"] {
            assert!(disk_image_app(Path::new(path)).is_some());
        }
        for path in ["/Applications/screator.app/Contents/MacOS/screator", "/Users/test/Applications/screator.app/Contents/MacOS/screator", "/tmp/target/release/screator", "/Volumes/screator/another.app/Contents/MacOS/screator"] {
            assert!(disk_image_app(Path::new(path)).is_none());
        }
    }

    #[test]
    fn first_install_copies_signed_bundle_and_opens_destination() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG 中文 ' $", "2.0.0");
        let destination = fixture.0.join("Applications/screator.app");
        let desktop = FakeDesktop::choosing(INSTALL);
        prompt_install(&source, &destination, &desktop).unwrap();
        assert_eq!(version(&destination), "2.0.0");
        assert!(source.exists());
        assert_eq!(*desktop.opened.borrow(), vec![destination]);
    }

    #[test]
    fn upgrade_reports_both_versions_replaces_and_removes_old_files() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        let destination = fixture.app("Applications", "1.0.0");
        fs::write(destination.join("obsolete"), "old worker").unwrap();
        let desktop = FakeDesktop::choosing(REPLACE);
        prompt_install(&source, &destination, &desktop).unwrap();
        assert_eq!(version(&destination), "2.0.0");
        assert!(!destination.join("obsolete").exists());
        assert!(desktop.messages.borrow()[0].contains("已安装版本：1.0.0"));
        assert!(desktop.messages.borrow()[0].contains("安装包版本：2.0.0"));
    }

    #[test]
    fn cancel_and_open_existing_leave_old_version_untouched() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        let destination = fixture.app("Applications", "1.0.0");
        for action in [CANCEL, OPEN] {
            let desktop = FakeDesktop::choosing(action);
            prompt_install(&source, &destination, &desktop).unwrap();
            assert_eq!(version(&destination), "1.0.0");
            assert_eq!(desktop.opened.borrow().len(), usize::from(action == OPEN));
            assert_eq!(desktop.checks.get(), 0);
        }
    }

    #[test]
    fn running_old_app_blocks_replacement_before_and_after_copy() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        let destination = fixture.app("Applications", "1.0.0");
        for running_on_check in [1, 2] {
            let desktop = FakeDesktop { running_on_check, ..FakeDesktop::choosing(REPLACE) };
            assert!(prompt_install(&source, &destination, &desktop).is_err());
            assert_eq!(version(&destination), "1.0.0");
            assert!(desktop.opened.borrow().is_empty());
        }
    }

    #[test]
    fn damaged_source_signature_keeps_old_app() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        fs::write(source.join("Contents/MacOS/screator"), "corrupt executable").unwrap();
        let destination = fixture.app("Applications", "1.0.0");
        assert!(prompt_install(&source, &destination, &FakeDesktop::choosing(REPLACE)).is_err());
        assert_eq!(version(&destination), "1.0.0");
    }

    #[test]
    fn failed_launch_restores_previous_version() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        let destination = fixture.app("Applications", "1.0.0");
        let desktop = FakeDesktop { fail_open: true, ..FakeDesktop::choosing(REPLACE) };
        assert!(prompt_install(&source, &destination, &desktop).is_err());
        assert_eq!(version(&destination), "1.0.0");
    }

    #[test]
    fn locates_user_install_and_refuses_symlink_or_unrelated_bundle() {
        let fixture = Fixture::new();
        let source = fixture.app("DMG", "2.0.0");
        let user_app = fixture.app("user/Applications", "1.0.0");
        let system = fixture.0.join("Applications");
        assert_eq!(installed_destination(&system, user_app.parent()), user_app);
        fs::create_dir_all(&system).unwrap();
        let destination = system.join(APP_NAME);
        symlink(&user_app, &destination).unwrap();
        assert!(prompt_install(&source, &destination, &FakeDesktop::choosing(REPLACE)).is_err());
        fs::remove_file(&destination).unwrap();
        let unrelated = fixture.app("Applications", "1.0.0");
        output(Command::new("/usr/bin/plutil").args(["-replace", "CFBundleIdentifier", "-string", "example.other"])
            .arg(unrelated.join("Contents/Info.plist"))).unwrap();
        assert!(prompt_install(&source, &unrelated, &FakeDesktop::choosing(REPLACE)).is_err());
        assert_eq!(plist_value(&unrelated, "CFBundleIdentifier").unwrap(), "example.other");
    }

    #[test]
    fn concurrent_installer_cannot_acquire_same_destination() {
        let fixture = Fixture::new();
        let first = InstallLock::acquire(&fixture.0).unwrap();
        assert!(InstallLock::acquire(&fixture.0).is_err());
        drop(first);
        assert!(InstallLock::acquire(&fixture.0).is_ok());
    }

}
