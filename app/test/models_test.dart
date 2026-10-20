import 'package:flutter_test/flutter_test.dart';
import 'package:smart_meeting_minutes/models.dart';

void main() {
  test('meeting parsing and live/finished flags', () {
    final m = Meeting.fromJson({
      'id': 'm1',
      'title': 'Standup',
      'status': 'capturing',
      'audio_confirmed': true,
      'participants': [
        {'name': 'Alice', 'joined_at': '2026-10-19T10:00:00+00:00', 'left_at': null},
        {'name': 'Bob', 'left_at': '2026-10-19T10:05:00+00:00'},
      ],
    });
    expect(m.status, MeetingStatus.capturing);
    expect(m.isLive, isTrue);
    expect(m.isFinished, isFalse);
    expect(m.audioConfirmed, isTrue);
    expect(m.participants.map((p) => p.present), [true, false]);
  });

  test('unknown or missing fields never throw', () {
    final m = Meeting.fromJson({'status': 'something_new'});
    expect(m.status, MeetingStatus.unknown);
    expect(m.title, 'Untitled meeting');
    expect(Meeting.fromJson(null).id, '');
  });

  test('failed meeting carries a retryable error', () {
    final m = Meeting.fromJson({'status': 'failed', 'error': {'code': 'ASR_FAILED', 'message': 'x', 'retryable': true}});
    expect(m.isFinished, isTrue);
    expect(m.error?.retryable, isTrue);
  });

  test('minutes keep null assignee/deadline as null (never invented)', () {
    final min = Minutes.fromJson({
      'meeting_id': 'm1',
      'meeting_title': 'T',
      'summary': 'S',
      'action_items': [
        {'task': 'Send notes', 'assignee': null, 'deadline': null, 'status': 'pending', 'evidence': 'I will send notes'},
        {'task': 'Fix bug', 'assignee': 'Alice', 'deadline': 'Friday'},
      ],
      'decisions': [
        {'description': 'Ship beta', 'speaker': null, 'context': '', 'evidence': 'we decided'},
      ],
      'transcript': [
        {'speaker': 'Alice', 'text': 'Hello', 'start_time': 1.5, 'end_time': 3, 'uncertain': true},
      ],
    });
    expect(min.actionItems[0].assignee, isNull);
    expect(min.actionItems[0].deadline, isNull);
    expect(min.actionItems[1].assignee, 'Alice');
    expect(min.decisions.single.speaker, isNull);
    expect(min.transcript.single.uncertain, isTrue);
    expect(min.transcript.single.end, 3.0);
  });

  test('items cite transcript lines and the source label names speakers and the first time', () {
    final min = Minutes.fromJson({
      'meeting_id': 'm1',
      'transcript': [
        {'id': 3, 'speaker': 'Alice', 'text': 'I will send the notes by Friday', 'start_time': 135, 'end_time': 140},
        {'id': 4, 'speaker': 'Bob', 'text': 'Thanks', 'start_time': 141, 'end_time': 142},
        {'id': 5, 'speaker': 'Alice', 'text': 'Welcome', 'start_time': 150, 'end_time': 151},
      ],
      'key_points': [
        {'topic': 'Notes', 'details': 'd', 'segment_ids': [3, 4, 5, 99]},
      ],
      'action_items': [
        {'task': 'Send notes', 'assignee': 'Alice', 'deadline': 'Friday', 'status': 'pending', 'evidence': 'e', 'segment_ids': [3]},
        {'task': 'Orphan', 'segment_ids': [99]},
        {'task': 'No ids'},
      ],
    });
    expect(min.keyPoints.single.segmentIds, [3, 4, 5, 99]);
    expect(min.sourceOf(min.keyPoints.single.segmentIds), 'Alice, Bob · from 02:15'); // 135 s, each speaker once, unknown id ignored
    expect(min.sourceOf(min.actionItems[0].segmentIds), 'Alice · from 02:15');
    expect(min.sourceOf(min.actionItems[1].segmentIds), isNull); // cites a line that does not exist
    expect(min.sourceOf(min.actionItems[2].segmentIds), isNull);
  });

  test('parsing tolerates wrong field types instead of throwing', () {
    final m = Meeting.fromJson({'status': 'completed', 'created_at': 12345, 'summary_preview': ['x'], 'participants': [{'name': 'A', 'joined_at': 7}]});
    expect(m.createdAt, isNull);
    expect(m.summaryPreview, isNull);
    expect(m.participants.single.joinedAt, isNull);
    final min = Minutes.fromJson({'key_points': [{'topic': 'T', 'details': 'D', 'segment_ids': ['a', 2, 3.0]}]});
    expect(min.keyPoints.single.segmentIds, [2, 3]);
  });

  test('formatting', () {
    expect(formatDuration(0), 'Not available');
    expect(formatDuration(45), '45s');
    expect(formatDuration(125), '2m 5s');
    expect(formatDuration(3700), '1h 1m');
    expect(formatClock(65), '01:05');
    expect(formatClock(3725), '1:02:05');
    expect(formatClock(-1), '00:00');
  });
}
