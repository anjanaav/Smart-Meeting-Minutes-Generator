import 'package:flutter/material.dart';

import '../auth_service.dart';
import '../services.dart';
import '../validators.dart';
import '../widgets.dart';

class SignUpScreen extends StatefulWidget {
  const SignUpScreen({super.key});
  @override
  State<SignUpScreen> createState() => _SignUpScreenState();
}

class _SignUpScreenState extends State<SignUpScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController(), _password = TextEditingController(), _confirm = TextEditingController();
  bool _busy = false, _hide = true;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await Services.of(context).auth.signUp(_email.text, _password.text);
      messenger.showSnackBar(const SnackBar(content: Text('Account created. You are signed in.')));
      navigator.popUntil((r) => r.isFirst); // the AuthGate underneath now shows the dashboard
    } on AuthFailure catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Sign Up')),
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Form(
                  key: _form,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    if (_error != null) ...[ErrorBanner(_error!), const SizedBox(height: 12)],
                    TextFormField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: 'Email', border: OutlineInputBorder()),
                      validator: Validators.email,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _password,
                      obscureText: _hide,
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: 'Password (8+ characters)',
                        border: const OutlineInputBorder(),
                        suffixIcon: IconButton(icon: Icon(_hide ? Icons.visibility : Icons.visibility_off), onPressed: () => setState(() => _hide = !_hide)),
                      ),
                      validator: (v) => Validators.password(v, strict: true),
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _confirm,
                      obscureText: _hide,
                      onFieldSubmitted: (_) => _busy ? null : _submit(),
                      decoration: const InputDecoration(labelText: 'Confirm password', border: OutlineInputBorder()),
                      validator: (v) => Validators.confirm(v, _password.text),
                    ),
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)) : const Text('Create Account'),
                    ),
                  ]),
                ),
              ),
            ),
          ),
        ),
      );
}
