import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../models.dart';
import '../services.dart';
import '../widgets.dart';
import 'minutes_screen.dart';

/// Live view of one meeting: bot joining, participants, audio capture and processing.
/// Every state shown here is read from the backend (GET /status); nothing is assumed or simulated.
class MeetingSessionScreen extends StatefulWidget {
  const MeetingSessionScreen({super.key, required this.meetingId, required this.serverUrl});
  final String meetingId, serverUrl;

  @override
  State<MeetingSessionScreen> createState() => _MeetingSessionScreenState();
}

class _MeetingSessionScreenState extends State<MeetingSessionScreen> {
  Meeting? _meeting;
  String? _networkError, _actionError;
  bool _stopping = false, _opened = false;
  Timer? _timer;
  ApiClient? _api;
  bool _polling = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_api != null) return;
    _api = Services.of(context).api(widget.serverUrl);
    _poll();
    _timer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _api?.close();
    super.dispose();
  }

  Future<void> _poll() async {
    if (_polling || !mounted) return;
    _polling = true;
    try {
      final m = await _api!.status(widget.meetingId);
      if (!mounted) return;
      setState(() {
        _meeting = m;
        _networkError = null;
      });
      if (m.isFinished) {
        _timer?.cancel();
        if (m.status == MeetingStatus.completed && !_opened) {
          _opened = true;
          _openMinutes();
        }
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _networkError = e.isNetwork ? 'Connection to the server lost. Retrying… (the status below may be out of date)' : e.message);
    } finally {
      _polling = false;
    }
  }

  void _openMinutes() {
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => MinutesScreen(meetingId: widget.meetingId, serverUrl: widget.serverUrl)));
  }

  Future<void> _stop() async {
    setState(() {
      _stopping = true;
      _actionError = null;
    });
    try {
      await _api!.stop(widget.meetingId);
      _poll();
    } on ApiException catch (e) {
      if (mounted) setState(() => _actionError = e.message);
    } finally {
      if (mounted) setState(() => _stopping = false);
    }
  }

  Future<void> _retry() async {
    setState(() => _actionError = null);
    try {
      await _api!.retry(widget.meetingId);
      _timer?.cancel();
      _timer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
      _poll();
    } on ApiException catch (e) {
      if (mounted) setState(() => _actionError = e.message);
    }
  }

  Future<void> _openJitsi(String url) async {
    try {
      final ok = await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      if (!ok && mounted) toast(context, 'Could not open the link.');
    } catch (_) {
      if (mounted) toast(context, 'Could not open the link.');
    }
  }

  // What the user sees for each backend state. "Joined" deliberately says audio is not confirmed yet.
  (IconData, String, String) _describe(Meeting m) => switch (m.status) {
        MeetingStatus.created => (Icons.hourglass_empty, 'Not started', 'The bot has not been sent yet.'),
        MeetingStatus.botJoining => (Icons.sync, 'Joining the meeting…', 'The bot is connecting to the Jitsi room.'),
        MeetingStatus.waiting => (Icons.lock_clock, 'Waiting for host approval', 'Ask the host to admit "Smart Meeting Minutes Bot" from the lobby.'),
        MeetingStatus.joined => (Icons.login, 'Bot joined', 'Waiting for someone to speak. Audio capture is not confirmed yet.'),
        MeetingStatus.capturing => (Icons.mic, 'Capturing audio', 'Audio has been received and confirmed by the server.'),
        MeetingStatus.disconnected => (Icons.wifi_off, 'Disconnected', 'The bot lost the connection and is trying to rejoin.'),
        MeetingStatus.processing => (Icons.auto_awesome, 'Generating minutes', 'Transcribing and analysing the discussion. This can take a few minutes.'),
        MeetingStatus.completed => (Icons.check_circle_outline, 'Minutes ready', 'Opening the minutes…'),
        MeetingStatus.failed => (Icons.error_outline, 'Meeting failed', ''),
        MeetingStatus.unknown => (Icons.help_outline, 'Unknown state', 'Update the app or check the server version.'),
      };

  @override
  Widget build(BuildContext context) {
    final m = _meeting;
    return Scaffold(
      appBar: AppBar(title: const Text('Meeting')),
      body: SafeArea(
        child: m == null
            ? Center(
                child: _networkError != null
                    ? Padding(padding: const EdgeInsets.all(16), child: ErrorBanner(_networkError!, onRetry: _poll))
                    : const CircularProgressIndicator(),
              )
            : ListView(padding: const EdgeInsets.all(16), children: [
                if (_networkError != null) ...[ErrorBanner(_networkError!), const SizedBox(height: 12)],
                if (_actionError != null) ...[ErrorBanner(_actionError!), const SizedBox(height: 12)],
                _statusCard(m),
                if (m.meetingUrl.isNotEmpty)
                  SectionCard(
                    title: 'Meeting link',
                    icon: Icons.link,
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      SelectableText(m.meetingUrl),
                      const SizedBox(height: 8),
                      Wrap(spacing: 8, children: [
                        OutlinedButton.icon(onPressed: () => _openJitsi(m.meetingUrl), icon: const Icon(Icons.open_in_new), label: const Text('Open in Jitsi')),
                        OutlinedButton.icon(
                          onPressed: () async {
                            await Clipboard.setData(ClipboardData(text: m.meetingUrl));
                            if (context.mounted) toast(context, 'Link copied');
                          },
                          icon: const Icon(Icons.copy),
                          label: const Text('Copy'),
                        ),
                      ]),
                    ]),
                  ),
                SectionCard(title: 'Participants', icon: Icons.group_outlined, child: _participants(m)),
                if (m.isLive)
                  FilledButton.tonalIcon(
                    onPressed: _stopping || m.stopRequested ? null : _stop,
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: Text(_stopping || m.stopRequested ? 'Stopping…' : 'End meeting and generate minutes'),
                  ),
              ]),
      ),
    );
  }

  Widget _statusCard(Meeting m) {
    final (icon, title, detail) = _describe(m);
    final failed = m.status == MeetingStatus.failed;
    final busy = m.status == MeetingStatus.botJoining || m.status == MeetingStatus.processing || m.status == MeetingStatus.disconnected;
    return SectionCard(
      title: title,
      icon: icon,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (busy) const Padding(padding: EdgeInsets.only(bottom: 10), child: LinearProgressIndicator()),
        if (failed && m.error != null) ErrorBanner(friendlyError(m.error!.code, m.error!.message)) else Text(m.statusDetail.isNotEmpty && !failed ? '$detail\n${m.statusDetail}' : detail),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: [
          Chip(
            avatar: Icon(m.audioConfirmed ? Icons.graphic_eq : Icons.mic_off, size: 18),
            label: Text(m.audioConfirmed ? 'Audio confirmed' : 'No audio confirmed yet'),
          ),
        ]),
        if (failed && (m.error?.retryable ?? false)) ...[
          const SizedBox(height: 8),
          FilledButton(onPressed: _retry, child: const Text('Retry processing')),
        ],
      ]),
    );
  }

  Widget _participants(Meeting m) {
    if (m.participants.isEmpty) {
      return const Text('Participant names are not available yet. They appear once the bot sees them in the meeting.');
    }
    return Column(children: [
      for (final p in m.participants)
        ListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          leading: Icon(p.present ? Icons.person : Icons.person_off_outlined),
          title: Text(p.name),
          subtitle: Text(p.present ? 'In the meeting' : 'Left'),
        ),
    ]);
  }
}
