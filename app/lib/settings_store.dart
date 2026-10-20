import 'package:shared_preferences/shared_preferences.dart';

/// Locally stored defaults for the New Meeting form. Nothing secret is stored here.
class AppSettings {
  final String serverUrl, displayName;
  const AppSettings({required this.serverUrl, required this.displayName});

  /// 10.0.2.2 is the host machine as seen from the Android emulator.
  static const defaultServerUrl = 'http://192.168.18.8:5000';
  AppSettings copyWith({String? serverUrl, String? displayName}) =>
      AppSettings(serverUrl: serverUrl ?? this.serverUrl, displayName: displayName ?? this.displayName);
}

class SettingsStore {
  static const _kServer = 'server_url', _kName = 'display_name';

  Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    return AppSettings(serverUrl: p.getString(_kServer) ?? AppSettings.defaultServerUrl, displayName: p.getString(_kName) ?? '');
  }

  Future<void> save(AppSettings s) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(_kServer, s.serverUrl.trim());
    await p.setString(_kName, s.displayName.trim());
  }
}
