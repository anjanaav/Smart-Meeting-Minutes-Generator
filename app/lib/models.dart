/// Data models mirroring docs/API.md. Parsing is tolerant: unknown or missing fields never crash the UI.
String _s(dynamic v, [String d = '']) => v is String ? v : d;
int _i(dynamic v, [int d = 0]) => v is num ? v.toInt() : d;
double _d(dynamic v, [double d = 0]) => v is num ? v.toDouble() : d;
String? _n(dynamic v) => v is String ? v : null;
List<dynamic> _l(dynamic v) => v is List ? v : const [];
List<int> _ints(dynamic v) => _l(v).whereType<num>().map((n) => n.toInt()).toList();
Map<String, dynamic> _m(dynamic v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};

class MeetingError {
  final String code, message;
  final bool retryable;
  const MeetingError(this.code, this.message, this.retryable);
  static MeetingError? from(dynamic v) {
    if (v is! Map) return null;
    final m = _m(v);
    return MeetingError(_s(m['code'], 'ERROR'), _s(m['message'], 'Something went wrong.'), m['retryable'] == true);
  }
}

class Participant {
  final String name;
  final String? joinedAt, leftAt;
  const Participant(this.name, {this.joinedAt, this.leftAt});
  bool get present => leftAt == null;
  factory Participant.fromJson(dynamic j) {
    final m = _m(j);
    return Participant(_s(m['name'], 'Participant'), joinedAt: _n(m['joined_at']), leftAt: _n(m['left_at']));
  }
}

enum MeetingStatus { created, botJoining, waiting, joined, capturing, disconnected, processing, completed, failed, unknown }

MeetingStatus parseStatus(String s) => switch (s) {
      'created' => MeetingStatus.created,
      'bot_joining' => MeetingStatus.botJoining,
      'waiting' => MeetingStatus.waiting,
      'joined' => MeetingStatus.joined,
      'capturing' => MeetingStatus.capturing,
      'disconnected' => MeetingStatus.disconnected,
      'processing' => MeetingStatus.processing,
      'completed' => MeetingStatus.completed,
      'failed' => MeetingStatus.failed,
      _ => MeetingStatus.unknown,
    };

class Meeting {
  final String id, title, roomName, meetingUrl, statusRaw, statusDetail;
  final MeetingStatus status;
  final MeetingError? error;
  final bool audioConfirmed, stopRequested;
  final String? createdAt, startedAt, endedAt, summaryPreview, generatedBy;
  final int durationSeconds;
  final List<Participant> participants;

  const Meeting({
    required this.id,
    required this.title,
    required this.roomName,
    required this.meetingUrl,
    required this.statusRaw,
    required this.status,
    required this.statusDetail,
    required this.error,
    required this.audioConfirmed,
    required this.stopRequested,
    required this.createdAt,
    required this.startedAt,
    required this.endedAt,
    required this.summaryPreview,
    required this.generatedBy,
    required this.durationSeconds,
    required this.participants,
  });

  factory Meeting.fromJson(dynamic j) {
    final m = _m(j);
    final raw = _s(m['status'], 'unknown');
    return Meeting(
      id: _s(m['id']),
      title: _s(m['title'], _s(m['room_name'], 'Untitled meeting')),
      roomName: _s(m['room_name']),
      meetingUrl: _s(m['meeting_url']),
      statusRaw: raw,
      status: parseStatus(raw),
      statusDetail: _s(m['status_detail']),
      error: MeetingError.from(m['error']),
      audioConfirmed: m['audio_confirmed'] == true,
      stopRequested: m['stop_requested'] == true,
      createdAt: _n(m['created_at']),
      startedAt: _n(m['started_at']),
      endedAt: _n(m['ended_at']),
      summaryPreview: _n(m['summary_preview']),
      generatedBy: _n(m['generated_by']),
      durationSeconds: _i(m['duration_seconds']),
      participants: _l(m['participants']).map(Participant.fromJson).toList(),
    );
  }

  bool get isLive => const {
        MeetingStatus.botJoining,
        MeetingStatus.waiting,
        MeetingStatus.joined,
        MeetingStatus.capturing,
        MeetingStatus.disconnected,
      }.contains(status);
  bool get isFinished => status == MeetingStatus.completed || status == MeetingStatus.failed;
}

class KeyPoint {
  final String topic, details;
  final List<int> segmentIds;
  const KeyPoint(this.topic, this.details, this.segmentIds);
}

class Decision {
  final String description, context, evidence;
  final String? speaker;
  final List<int> segmentIds;
  const Decision(this.description, this.speaker, this.context, this.evidence, this.segmentIds);
}

class ActionItem {
  final String task, status, evidence;
  final String? assignee, deadline;
  final List<int> segmentIds;
  const ActionItem(this.task, this.assignee, this.deadline, this.status, this.evidence, this.segmentIds);
}

class TranscriptLine {
  final int id;
  final String speaker, text;
  final double start, end;
  final bool uncertain;
  const TranscriptLine(this.id, this.speaker, this.text, this.start, this.end, this.uncertain);
}

class Minutes {
  final String meetingId, title, summary, generatedBy;
  final String? date;
  final int durationSeconds;
  final List<KeyPoint> keyPoints;
  final List<Decision> decisions;
  final List<ActionItem> actionItems;
  final List<String> participants, warnings;
  final List<TranscriptLine> transcript;

  const Minutes({
    required this.meetingId,
    required this.title,
    required this.summary,
    required this.generatedBy,
    required this.date,
    required this.durationSeconds,
    required this.keyPoints,
    required this.decisions,
    required this.actionItems,
    required this.participants,
    required this.warnings,
    required this.transcript,
  });

  /// Where in the meeting an item came from: the speakers of the transcript lines it cites and the time of the
  /// first one, for example "Alice, Bob · from 02:15". Null when none of the cited lines exist.
  String? sourceOf(List<int> segmentIds) {
    if (segmentIds.isEmpty) return null;
    final wanted = segmentIds.toSet();
    final lines = transcript.where((l) => wanted.contains(l.id)).toList();
    if (lines.isEmpty) return null;
    final speakers = <String>[];
    for (final l in lines) {
      if (!speakers.contains(l.speaker)) speakers.add(l.speaker);
    }
    return '${speakers.join(', ')} · from ${formatClock(lines.first.start)}';
  }

  factory Minutes.fromJson(dynamic j) {
    final m = _m(j);
    String? nz(dynamic v) => v is String && v.trim().isNotEmpty ? v : null;
    return Minutes(
      meetingId: _s(m['meeting_id']),
      title: _s(m['meeting_title'], 'Meeting'),
      summary: _s(m['summary']),
      generatedBy: _s(m['generated_by']),
      date: nz(m['meeting_date']),
      durationSeconds: _i(m['duration_seconds']),
      keyPoints: _l(m['key_points']).map((e) {
        final k = _m(e);
        return KeyPoint(_s(k['topic']), _s(k['details']), _ints(k['segment_ids']));
      }).toList(),
      decisions: _l(m['decisions']).map((e) {
        final k = _m(e);
        return Decision(_s(k['description']), nz(k['speaker']), _s(k['context']), _s(k['evidence']), _ints(k['segment_ids']));
      }).toList(),
      actionItems: _l(m['action_items']).map((e) {
        final k = _m(e);
        return ActionItem(_s(k['task']), nz(k['assignee']), nz(k['deadline']), _s(k['status'], 'pending'), _s(k['evidence']), _ints(k['segment_ids']));
      }).toList(),
      participants: _l(m['participants']).map((e) => _s(_m(e)['name'])).where((n) => n.isNotEmpty).toList(),
      warnings: _l(m['warnings']).whereType<String>().toList(),
      transcript: _l(m['transcript']).map((e) {
        final k = _m(e);
        return TranscriptLine(_i(k['id'], -1), _s(k['speaker'], 'Speaker'), _s(k['text']), _d(k['start_time']), _d(k['end_time']), k['uncertain'] == true);
      }).toList(),
    );
  }
}

class Health {
  final bool ok, asrAvailable, llmConfigured;
  final String store;
  const Health(this.ok, this.asrAvailable, this.llmConfigured, this.store);
  factory Health.fromJson(dynamic j) {
    final m = _m(j);
    return Health(m['ok'] == true, _m(m['asr'])['available'] == true, _m(m['llm'])['configured'] == true, _s(m['store']));
  }
}

String formatDuration(int seconds) {
  if (seconds <= 0) return 'Not available';
  final h = seconds ~/ 3600, m = (seconds % 3600) ~/ 60, s = seconds % 60;
  if (h > 0) return '${h}h ${m}m';
  if (m > 0) return '${m}m ${s}s';
  return '${s}s';
}

String formatClock(double seconds) {
  final t = seconds.isFinite && seconds > 0 ? seconds.floor() : 0;
  final h = t ~/ 3600, m = (t % 3600) ~/ 60, s = t % 60;
  String two(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '${two(m)}:${two(s)}';
}
