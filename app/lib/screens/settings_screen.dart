import 'package:flutter/material.dart';

import '../services.dart';
import '../settings_store.dart';
import '../validators.dart';
import '../widgets.dart';

/// Only the defaults the New Meeting form uses, plus the signed-in account.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController(), _server = TextEditingController();
  bool _loaded = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_loaded) return;
    _loaded = true;
    Services.of(context).settings.load().then((s) {
      if (!mounted) return;
      setState(() {
        _name.text = s.displayName;
        _server.text = s.serverUrl;
      });
    });
  }

  @override
  void dispose() {
    _name.dispose();
    _server.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    await Services.of(context).settings.save(AppSettings(serverUrl: _server.text, displayName: _name.text));
    if (mounted) toast(context, 'Settings saved');
  }

  @override
  Widget build(BuildContext context) {
    final auth = Services.of(context).auth;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(16), children: [
          ListTile(contentPadding: EdgeInsets.zero, leading: const Icon(Icons.account_circle_outlined), title: Text(auth.user?.email ?? 'Signed in'), subtitle: const Text('Signed-in account')),
          const SizedBox(height: 8),
          Form(
            key: _form,
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              TextFormField(controller: _name, decoration: const InputDecoration(labelText: 'Default user name', border: OutlineInputBorder()), validator: (v) => (v ?? '').trim().isEmpty ? null : Validators.userName(v)),
              const SizedBox(height: 12),
              TextFormField(controller: _server, keyboardType: TextInputType.url, decoration: const InputDecoration(labelText: 'Default backend server URL', border: OutlineInputBorder()), validator: Validators.serverUrl),
              const SizedBox(height: 16),
              FilledButton(onPressed: _save, child: const Text('Save')),
            ]),
          ),
          const Divider(height: 40),
          OutlinedButton.icon(
            onPressed: () async {
              Navigator.of(context).popUntil((r) => r.isFirst);
              await auth.signOut();
            },
            icon: const Icon(Icons.logout),
            label: const Text('Log out'),
          ),
        ]),
      ),
    );
  }
}
