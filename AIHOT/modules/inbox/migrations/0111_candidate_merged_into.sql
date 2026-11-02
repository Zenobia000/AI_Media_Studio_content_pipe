-- Two candidates a person merged before opening: the merged one points at the one that carries the topic.
ALTER TABLE candidate_state ADD COLUMN merged_into text
