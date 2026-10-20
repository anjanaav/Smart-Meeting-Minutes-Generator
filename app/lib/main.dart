import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';

import 'package:flutter/material.dart';

import 'auth_service.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'services.dart';
import 'settings_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  String? firebaseError;
  try {
    // Android reads android/app/google-services.json (see README). No keys live in the Dart code.
    await Firebase.initializeApp();
  } catch (e) {
    debugPrint('Firebase initialisation failed: $e');
    firebaseError = 'Firebase is not configured for this build. Add google-services.json (see the app README).';
  }
  runApp(SmmgApp(firebaseError: firebaseError));
}

class SmmgApp extends StatefulWidget {
  const SmmgApp({super.key, this.firebaseError});
  final String? firebaseError;

  @override
  State<SmmgApp> createState() => _SmmgAppState();
}

class _SmmgAppState extends State<SmmgApp> {
  // Created once, so the services (and the widgets that depend on them) are stable across rebuilds.
  AuthService? _auth;
  final SettingsStore _settings = SettingsStore();

  @override
  void initState() {
    super.initState();
    if (widget.firebaseError == null) _auth = AuthService();
  }

  @override
  Widget build(BuildContext context) {
    final theme = ThemeData(colorSchemeSeed: const Color(0xFF3F51B5), useMaterial3: true);
    final auth = _auth;
    if (auth == null) {
      return MaterialApp(
        title: 'Smart Meeting Minutes',
        theme: theme,
        home: Scaffold(
          body: Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(widget.firebaseError ?? 'Firebase is not configured.', textAlign: TextAlign.center))),
        ),
      );
    }
    return Services(
      auth: auth,
      settings: _settings,
      child: MaterialApp(title: 'Smart Meeting Minutes', theme: theme, home: const AuthGate()),
    );
  }
}

/// Shows the dashboard while signed in and the login screen otherwise. The session itself is kept by Firebase.
class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) => StreamBuilder<User?>(
        stream: Services.of(context).auth.changes,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Scaffold(body: Center(child: CircularProgressIndicator()));
          }
          return snap.data == null ? const LoginScreen() : const HomeScreen();
        },
      );
}
