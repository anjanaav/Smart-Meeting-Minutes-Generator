import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../services.dart';
import '../settings_store.dart';
import '../widgets.dart';
import 'meeting_session_screen.dart';
import 'minutes_screen.dart';

/// Screen 9: the signed-in user's saved meetings (the backend only ever returns the caller's own records).
class PastMeetingsScreen extends StatefulWidget {
  const PastMeetingsScreen({super.key});
  @override
  State<PastMeetingsScreen> createState() => _PastMeetingsScreenState();
}

class _PastMeetingsScreenState extends State<PastMeetingsScreen> {
  List<Meeting>? _meetings;
  String? _error;
  bool _loading = true;
  String _serverUrl = AppSettings.defaultServerUrl;
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final services = Services.of(context);
    try {
      _serverUrl = (await services.settings.load()).serverUrl;
      final api = services.api(_serverUrl);
      try {
        final list = await api.listMeetings();
        if (mounted) setState(() => _meetings = list);
      } finally {
        api.close();
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _open(Meeting m) async {
    final route = m.status == MeetingStatus.completed
        ? MaterialPageRoute<void>(builder: (_) => MinutesScreen(meetingId: m.id, serverUrl: _serverUrl))
        : MaterialPageRoute<void>(builder: (_) => MeetingSessionScreen(meetingId: m.id, serverUrl: _serverUrl));
    await Navigator.of(context).push(route);
    if (mounted) _load();
  }

  String _statusLabel(Meeting m) => switch (m.status) {
        MeetingStatus.completed => 'Completed',
        MeetingStatus.failed => 'Failed',
        MeetingStatus.processing => 'Processing',
        MeetingStatus.created => 'Not started',
        _ => 'In progress',
      };

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Past Meetings'), actions: [IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh), tooltip: 'Refresh')]),
        body: SafeArea(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? Padding(padding: const EdgeInsets.all(16), child: ErrorBanner(_error!, onRetry: _load))
                  : (_meetings ?? const []).isEmpty
                      ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('No meetings yet. Start one from New Meeting.', textAlign: TextAlign.center)))
                      : RefreshIndicator(
                          onRefresh: _load,
                          child: ListView.separated(
                            itemCount: _meetings!.length,
                            separatorBuilder: (_, __) => const Divider(height: 1),
                            itemBuilder: (_, i) {
                              final m = _meetings![i];
                              final preview = (m.summaryPreview ?? '').trim();
                              return ListTile(
                                leading: Icon(m.status == MeetingStatus.completed ? Icons.description_outlined : m.status == MeetingStatus.failed ? Icons.error_outline : Icons.pending_outlined),
                                title: Text(m.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(
                                  [formatDateTime(m.createdAt), _statusLabel(m), if (m.durationSeconds > 0) formatDuration(m.durationSeconds), if (preview.isNotEmpty) preview].join(' · '),
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                ),
                                trailing: const Icon(Icons.chevron_right),
                                onTap: () => _open(m),
                              );
                            },
                          ),
                        ),
        ),
      );
}
