import 'package:firebase_auth/firebase_auth.dart';

/// Thin wrapper over Firebase Authentication (email + password). Passwords never touch our own backend.
class AuthService {
  AuthService([FirebaseAuth? auth]) : _auth = auth ?? FirebaseAuth.instance;
  final FirebaseAuth _auth;

  Stream<User?> get changes => _auth.authStateChanges();
  User? get user => _auth.currentUser;

  /// Fresh ID token for backend calls (the SDK refreshes it when it is about to expire).
  // DEVELOPMENT ONLY: matches backend AUTH_MODE=dev.
// Restore Firebase ID tokens before production use.
Future<String?> idToken() async {
  final user = _auth.currentUser;
  if (user == null) return null;

  return 'dev:${user.uid}:${user.email ?? ''}';
}

  Future<void> signIn(String email, String password) async {
    try {
      await _auth.signInWithEmailAndPassword(email: email.trim(), password: password);
    } on FirebaseAuthException catch (e) {
      throw AuthFailure(authMessage(e.code));
    }
  }

  Future<void> signUp(String email, String password) async {
    try {
      await _auth.createUserWithEmailAndPassword(email: email.trim(), password: password);
    } on FirebaseAuthException catch (e) {
      throw AuthFailure(authMessage(e.code));
    }
  }

  Future<void> signOut() => _auth.signOut();
}

class AuthFailure implements Exception {
  final String message;
  const AuthFailure(this.message);
  @override
  String toString() => message;
}

/// Maps Firebase error codes to messages a user can act on.
String authMessage(String code) => switch (code) {
      'invalid-credential' || 'wrong-password' || 'user-not-found' || 'invalid-login-credentials' => 'Incorrect email or password.',
      'invalid-email' => 'Enter a valid email address.',
      'user-disabled' => 'This account has been disabled.',
      'email-already-in-use' => 'An account with this email already exists. Sign in instead.',
      'weak-password' => 'Choose a stronger password (at least 8 characters).',
      'too-many-requests' => 'Too many attempts. Wait a moment and try again.',
      'network-request-failed' => 'No network connection. Check your internet and try again.',
      'operation-not-allowed' => 'Email/password sign-in is not enabled for this app.',
      _ => 'Authentication failed. Please try again.',
    };
