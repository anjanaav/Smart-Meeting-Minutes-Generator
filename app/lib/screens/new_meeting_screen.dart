import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../services.dart';
import '../settings_store.dart';
import '../validators.dart';
import '../widgets.dart';
import 'meeting_session_screen.dart';

enum JoinMode { link, auto }

class NewMeetingScreen extends StatefulWidget {
  const NewMeetingScreen({super.key});
  @override
  State<NewMeetingScreen> createState() => _NewMeetingScreenState();
}

class _NewMeetingScreenState extends State<NewMeetingScreen> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController(), _link = TextEditingController(), _room = TextEditingController(), _server = TextEditingController();
  JoinMode _mode = JoinMode.link;
  bool _openInApp = true, _consent = false, _testing = false, _starting = false, _loaded = false;
  String? _error;
  String? _testResult;
  bool _testOk = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_loaded) return;
    _loaded = true;
    Services.of(context).settings.load().then((s) {
      if (!mounted) return;
      setState(() {
        if (_server.text.isEmpty) _server.text = s.serverUrl;
        if (_name.text.isEmpty) _name.text = s.displayName;
      });
    });
  }

  @override
  void dispose() {
    for (final c in [_name, _link, _room, _server]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _test() async {
    final bad = Validators.serverUrl(_server.text);
    if (bad != null) {
      setState(() {
        _testOk = false;
        _testResult = bad;
      });
      return;
    }
    setState(() {
      _testing = true;
      _testResult = null;
    });
    final api = Services.of(context).api(_server.text);
    try {
      final h = await api.health();
      if (!mounted) return;
      setState(() {
        _testOk = h.ok;
        _testResult = h.ok
            ? 'Backend reachable. Speech recognition: ${h.asrAvailable ? 'available' : 'NOT available'}. '
                'Minutes generation: ${h.llmConfigured ? 'AI model' : 'rule-based (no AI key configured)'}.'
            : 'The server answered but reports a problem.';
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _testOk = false;
          _testResult = e.message;
        });
      }
    } finally {
      api.close();
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _openJitsi(String url) async {
    try {
      final ok = await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      if (!ok && mounted) toast(context, 'Could not open Jitsi Meet. Open the link manually: $url');
    } catch (_) {
      if (mounted) toast(context, 'Could not open Jitsi Meet. Open the link manually: $url');
    }
  }

  Future<void> _start() async {
    setState(() => _error = null);
    final linkError = Validators.jitsiTarget(link: _link.text, room: _room.text, requireOne: _mode == JoinMode.link);
    if (!_form.currentState!.validate() || linkError != null) {
      if (linkError != null) setState(() => _error = linkError);
      return;
    }
    if (!_consent) {
      setState(() => _error = 'Confirm that all participants agree to the meeting being recorded and transcribed.');
      return;
    }
    final services = Services.of(context);
    final navigator = Navigator.of(context);
    setState(() => _starting = true);
    final api = services.api(_server.text);
    try {
      await services.settings.save(AppSettings(serverUrl: _server.text, displayName: _name.text));
      final auto = _mode == JoinMode.auto;
      final created = await api.createMeeting(
        meetingUrl: auto ? null : _link.text,
        roomName: auto ? null : _room.text,
        displayName: _name.text,
        consent: _consent,
      );
      // Starts the bot. The backend only reports "joined" / "capturing" once the bot has confirmed it.
      final meeting = await api.joinBot(created.meeting.id);
      if (_openInApp && meeting.meetingUrl.isNotEmpty) await _openJitsi(meeting.meetingUrl);
      if (!mounted) return;
      await navigator.push(MaterialPageRoute(builder: (_) => MeetingSessionScreen(meetingId: meeting.id, serverUrl: _server.text.trim())));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      api.close();
      if (mounted) setState(() => _starting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auto = _mode == JoinMode.auto;
    final busy = _starting || _testing;
    return Scaffold(
      appBar: AppBar(title: const Text('New Meeting')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Form(
            key: _form,
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              if (_error != null) ...[ErrorBanner(_error!), const SizedBox(height: 12)],
              TextFormField(
                controller: _name,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(labelText: 'User name', border: OutlineInputBorder()),
                validator: Validators.userName,
              ),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: _mode == JoinMode.link
                      ? FilledButton.icon(onPressed: null, icon: const Icon(Icons.link), label: const Text('Join via Link'))
                      : OutlinedButton.icon(onPressed: busy ? null : () => setState(() => _mode = JoinMode.link), icon: const Icon(Icons.link), label: const Text('Join via Link')),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: auto
                      ? FilledButton.icon(onPressed: null, icon: const Icon(Icons.auto_awesome), label: const Text('Auto'))
                      : OutlinedButton.icon(onPressed: busy ? null : () => setState(() => _mode = JoinMode.auto), icon: const Icon(Icons.auto_awesome), label: const Text('Auto')),
                ),
              ]),
              const SizedBox(height: 4),
              Text(
                auto ? 'Auto creates a new Jitsi room for you. Share its link with the other participants.' : 'Paste the Jitsi meeting link, or enter a room name below.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _link,
                enabled: !auto,
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(labelText: 'Jitsi meeting link', hintText: 'https://meet.jit.si/MyRoom', border: OutlineInputBorder()),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _room,
                enabled: !auto,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(labelText: 'Room name (optional)', border: OutlineInputBorder()),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _server,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                  labelText: 'Backend server URL',
                  border: const OutlineInputBorder(),
                  suffixIcon: TextButton(onPressed: busy ? null : _test, child: _testing ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Text('Test')),
                ),
                validator: Validators.serverUrl,
              ),
              if (_testResult != null) ...[
                const SizedBox(height: 8),
                Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Icon(_testOk ? Icons.check_circle_outline : Icons.error_outline, size: 18, color: _testOk ? Colors.green : Theme.of(context).colorScheme.error),
                  const SizedBox(width: 6),
                  Expanded(child: Text(_testResult!, style: Theme.of(context).textTheme.bodySmall)),
                ]),
              ],
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Open in Jitsi Meet app'),
                subtitle: const Text('Opens the meeting link in the Jitsi app or your browser after the bot is sent.'),
                value: _openInApp,
                onChanged: busy ? null : (v) => setState(() => _openInApp = v),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: const Text('All participants agree to this meeting being recorded and transcribed.'),
                value: _consent,
                onChanged: busy ? null : (v) => setState(() => _consent = v ?? false),
              ),
              const SizedBox(height: 8),
              FilledButton(
                onPressed: busy ? null : _start,
                child: _starting ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)) : const Text('Start Meeting'),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
