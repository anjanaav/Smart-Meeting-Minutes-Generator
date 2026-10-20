import 'package:flutter/widgets.dart';

import 'api_client.dart';
import 'auth_service.dart';
import 'settings_store.dart';

/// App-wide services, provided through the widget tree (no extra state-management package needed).
class Services extends InheritedWidget {
  const Services({super.key, required this.auth, required this.settings, required super.child});

  final AuthService auth;
  final SettingsStore settings;

  static Services of(BuildContext context) {
    final s = context.dependOnInheritedWidgetOfExactType<Services>();
    assert(s != null, 'Services missing from the widget tree');
    return s!;
  }

  /// A client for the given backend URL that signs every request with the current Firebase ID token.
  ApiClient api(String baseUrl) => ApiClient(baseUrl: baseUrl, tokenProvider: auth.idToken);

  @override
  bool updateShouldNotify(Services old) => auth != old.auth || settings != old.settings;
}
